from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from rexecop.catalog.digest import canonical_digest
from rexecop.errors import RExecOpValidationError
from rexecop.operation.model import Operation, utc_now_iso
from rexecop.operation.plan import OperationPlan
from rexecop.operation.state import OperationState

ROLLBACK_MODES = frozenset({"observe", "dry_run", "apply", "emergency_readonly", "recovery"})


@dataclass(frozen=True)
class PreparedRollback:
    operation: Operation
    plan: OperationPlan


def rollback_failure_authority_digest(
    parent: Operation,
    parent_plan: OperationPlan,
) -> str:
    shared_state = parent.metadata.get("shared_state")
    terminal_receipt = (
        shared_state.get("execution_receipt")
        if isinstance(shared_state, dict)
        else None
    )
    return "sha256:" + canonical_digest(
        {
            "parent_operation_id": parent.id,
            "parent_state": parent.state,
            "last_failure": deepcopy(parent.metadata.get("last_failure") or {}),
            "terminal_execution_receipt": deepcopy(terminal_receipt or {}),
            "parent_plan_digest": "sha256:" + canonical_digest(parent_plan.as_dict()),
        }
    )


class RollbackExecutor:
    """Prepare rollback authority for execution by the ordinary orchestrator."""

    @staticmethod
    def operation_id(parent_operation_id: str) -> str:
        # Determinism is the recovery anchor if a process stops between writing
        # the child and writing the reciprocal link on the parent.
        return f"{parent_operation_id}-rollback"

    def can_prepare(self, operation: Operation, plan: OperationPlan) -> bool:
        if operation.state != OperationState.FAILED.value:
            return False
        if not plan.rollback_available:
            return False
        rollback = plan.workflow.get("rollback")
        if not isinstance(rollback, dict):
            return False
        steps = rollback.get("steps")
        return isinstance(steps, list) and bool(steps)

    def prepare(
        self,
        *,
        parent: Operation,
        parent_plan: OperationPlan,
        correlation_id: str,
    ) -> PreparedRollback:
        if not self.can_prepare(parent, parent_plan):
            raise RExecOpValidationError("rollback not defined for this failed operation")

        rollback = parent_plan.workflow["rollback"]
        raw_steps = rollback.get("steps")
        assert isinstance(raw_steps, list)
        if any(not isinstance(step, dict) for step in raw_steps):
            raise RExecOpValidationError("rollback steps must be mappings")
        steps = [deepcopy(step) for step in raw_steps]
        mode = str(rollback.get("mode") or "dry_run")
        if mode not in ROLLBACK_MODES:
            raise RExecOpValidationError(f"unsupported rollback mode: {mode}")

        child_id = self.operation_id(parent.id)
        parent_plan_digest = "sha256:" + canonical_digest(parent_plan.as_dict())
        failure_authority_digest = rollback_failure_authority_digest(parent, parent_plan)
        rollback_scope = {
            "mode": mode,
            "steps": steps,
        }
        rollback_plan_digest = "sha256:" + canonical_digest(rollback_scope)
        parent_workflow_id = str(parent_plan.workflow.get("id") or parent.intent)
        workflow = {
            "id": f"{parent_workflow_id}.rollback",
            "intent": parent.intent,
            "mode": mode,
            "risk": parent_plan.risk,
            "description": "Persisted rollback operation derived from an explicit workflow block.",
            "steps": steps,
            "retry": {
                "max_attempts": 0,
                "allowed_on": [],
                "blocked_on": ["outcome_indeterminate"],
            },
            "rollback": {},
        }
        required_connectors = list(
            dict.fromkeys(
                str(step.get("connector") or "")
                for step in steps
                if str(step.get("type") or "") == "connector"
                and str(step.get("connector") or "")
            )
        )
        pause_safe_points = [
            str(step.get("id") or "")
            for step in steps
            if step.get("pause_safe") is True and str(step.get("id") or "")
        ]
        preview: dict[str, Any] = {
            "operation_kind": "rollback",
            "parent_operation_id": parent.id,
            "parent_plan_digest": parent_plan_digest,
            "failure_authority_digest": failure_authority_digest,
            "rollback_plan_digest": rollback_plan_digest,
            "rollback_mode": mode,
            "rollback_steps": deepcopy(steps),
            "note": "preview only; not a governance decision",
        }

        now = utc_now_iso()
        operation = Operation(
            id=child_id,
            profile=parent.profile,
            environment=parent.environment,
            intent=parent.intent,
            target=parent.target,
            mode=mode,
            requested_by=parent.requested_by,
            state=OperationState.CREATED.value,
            created_at=now,
            updated_at=now,
            correlation_id=correlation_id,
        )
        for key in (
            "profile_root",
            "environment_path",
            "environment_connectors",
            "runtime_policy",
            "target_criticality",
            "http_action_bindings",
            "policy_pack",
            "catalog_binding",
            "catalog_runtime",
        ):
            if key in parent.metadata:
                operation.metadata[key] = deepcopy(parent.metadata[key])
        # The current workflow rollback block declares no input projection.
        # Start the derived operation from an isolated state; parent runtime
        # result namespaces are never implicit rollback inputs.
        operation.metadata["shared_state"] = {}
        operation.metadata["derived_operation"] = {
            "kind": "rollback",
            "parent_operation_id": parent.id,
            "parent_plan_digest": parent_plan_digest,
            "failure_authority_digest": failure_authority_digest,
            "rollback_plan_digest": rollback_plan_digest,
            "fresh_plan_governance_required": True,
            "fresh_attempt_governance_required": bool(required_connectors),
        }

        plan = OperationPlan(
            operation_id=child_id,
            profile=parent_plan.profile,
            environment=parent_plan.environment,
            intent=parent_plan.intent,
            target=parent_plan.target,
            mode=mode,
            workflow=workflow,
            planned_steps=steps,
            required_connectors=required_connectors,
            risk=parent_plan.risk,
            govengine_request_preview=preview,
            expected_evidence=[
                "plan_generated",
                "state_transition",
                "govengine_decision",
                "execution_attempt",
                "execution_receipt",
            ],
            pause_safe_points=pause_safe_points,
            retry_policy_summary=dict(workflow["retry"]),
            rollback_available=False,
            catalog_binding=deepcopy(parent_plan.catalog_binding),
        )
        return PreparedRollback(operation=operation, plan=plan)
