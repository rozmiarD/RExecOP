from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path

from rexecop.adapters.govengine_port.adapter import default_govengine_adapter, is_mutating_mode
from rexecop.adapters.govengine_port.contracts import (
    BLOCKING_DECISIONS,
    WAITING_DECISIONS,
    GovEngineAdapter,
    GovEngineDecision,
    GovEngineDecisionType,
    GovEngineRequest,
)
from rexecop.environment.loader import load_environment
from rexecop.errors import RExecOpValidationError
from rexecop.evidence.event import EvidenceEventType
from rexecop.evidence.manager import EvidenceManager
from rexecop.operation.model import Operation, StateTransitionRecord, utc_now_iso
from rexecop.operation.plan import OperationPlan
from rexecop.operation.state import OperationState, validate_transition
from rexecop.profile.loader import load_profile
from rexecop.storage.file_store import FileStore
from rexecop.workflow.loader import load_workflow

DEFAULT_MODE = "dry_run"
SUPPORTED_MODES = frozenset(
    {"observe", "dry_run", "apply", "emergency_readonly", "recovery"}
)


def generate_operation_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    short_id = secrets.token_hex(3)
    return f"op-{stamp}-{short_id}"


class OperationController:
    def __init__(
        self,
        store: FileStore | None = None,
        govengine_adapter: GovEngineAdapter | None = None,
    ) -> None:
        self.store = store or FileStore()
        self.evidence = EvidenceManager(self.store)
        self.govengine_adapter = govengine_adapter or default_govengine_adapter()

    def plan(
        self,
        *,
        profile_path: Path,
        environment_path: Path,
        intent: str,
        target: str,
        mode: str = DEFAULT_MODE,
        requested_by: str = "operator",
    ) -> Operation:
        if mode not in SUPPORTED_MODES:
            raise RExecOpValidationError(f"unsupported mode: {mode}")

        profile = load_profile(profile_path)
        environment = load_environment(environment_path)
        if environment.profile and environment.profile != profile.name:
            raise RExecOpValidationError(
                f"environment profile {environment.profile} does not match {profile.name}"
            )

        intent_meta = profile.intent_metadata(intent)
        workflow_path = profile.resolve_workflow_path(intent)
        workflow = load_workflow(workflow_path)

        operation_id = generate_operation_id()
        correlation_id = str(uuid.uuid4())
        now = utc_now_iso()

        operation = Operation(
            id=operation_id,
            profile=profile.name,
            environment=environment.id,
            intent=intent,
            target=target,
            mode=mode,
            requested_by=requested_by,
            state=OperationState.CREATED.value,
            created_at=now,
            updated_at=now,
            correlation_id=correlation_id,
        )

        created_event = self.evidence.emit(
            operation_id=operation.id,
            event_type=EvidenceEventType.OPERATION_CREATED,
            correlation_id=correlation_id,
            state_after=operation.state,
            payload={
                "profile": profile.name,
                "environment": environment.id,
                "intent": intent,
                "target": target,
                "mode": mode,
            },
        )
        operation.evidence_event_ids.append(created_event)

        plan = OperationPlan(
            operation_id=operation.id,
            profile=profile.name,
            environment=environment.id,
            intent=intent,
            target=target,
            mode=mode,
            workflow=workflow.as_dict(),
            planned_steps=[step.as_dict() for step in workflow.steps],
            required_connectors=workflow.required_connectors(),
            risk=str(intent_meta.get("risk") or workflow.risk),
            govengine_request_preview={
                "profile": profile.name,
                "environment": environment.id,
                "intent": intent,
                "target": target,
                "mode": mode,
                "risk": str(intent_meta.get("risk") or workflow.risk),
                "note": "preview only; not a governance decision",
            },
            expected_evidence=[
                "plan_generated",
                "state_transition",
            ],
            pause_safe_points=workflow.pause_safe_points(),
            retry_policy_summary=dict(workflow.retry or {"max_attempts": 0}),
            rollback_available=bool(workflow.rollback),
        )

        self._transition(
            operation,
            OperationState.PLANNED,
            reason="plan_completed",
            correlation_id=correlation_id,
        )

        plan_event = self.evidence.emit(
            operation_id=operation.id,
            event_type=EvidenceEventType.PLAN_GENERATED,
            correlation_id=correlation_id,
            state_before=OperationState.CREATED.value,
            state_after=operation.state,
            payload={"planned_steps": plan.planned_steps, "workflow_id": workflow.id},
        )
        operation.evidence_event_ids.append(plan_event)

        self.store.save_plan(plan)

        if is_mutating_mode(mode):
            decision = self._evaluate_governance(operation, plan, correlation_id)
            self._apply_governance_transition(operation, decision, correlation_id)

        self.store.save_operation(operation)
        return operation

    def evaluate_governance(self, operation_id: str) -> GovEngineDecision:
        operation = self.get_operation(operation_id)
        plan = self.store.load_plan(operation_id)
        return self._evaluate_governance(operation, plan, operation.correlation_id)

    def allows_mutating_execution(self, operation_id: str) -> bool:
        operation = self.get_operation(operation_id)
        if not is_mutating_mode(operation.mode):
            return False
        if operation.govengine_decision_type != GovEngineDecisionType.ALLOWED.value:
            return False
        return operation.state == OperationState.APPROVED.value

    def get_operation(self, operation_id: str) -> Operation:
        return self.store.load_operation(operation_id)

    def get_history(self, operation_id: str) -> dict[str, object]:
        operation = self.get_operation(operation_id)
        evidence = self.store.list_evidence_events(operation_id)
        return {
            "operation_id": operation.id,
            "state": operation.state,
            "govengine_decision_type": operation.govengine_decision_type,
            "govengine_decision_summary": operation.govengine_decision_summary,
            "transitions": [item.as_dict() for item in operation.history],
            "evidence_events": evidence,
        }

    def _evaluate_governance(
        self,
        operation: Operation,
        plan: OperationPlan,
        correlation_id: str,
    ) -> GovEngineDecision:
        request = GovEngineRequest(
            operation_id=operation.id,
            profile=plan.profile,
            environment=plan.environment,
            intent=plan.intent,
            target=plan.target,
            mode=plan.mode,
            risk=plan.risk,
            preview=plan.govengine_request_preview,
        )

        requested_event = self.evidence.emit(
            operation_id=operation.id,
            event_type=EvidenceEventType.GOVENGINE_DECISION_REQUESTED,
            correlation_id=correlation_id,
            state_before=operation.state,
            payload=request.as_dict(),
        )
        operation.evidence_event_ids.append(requested_event)

        decision = self.govengine_adapter.evaluate(request)
        operation.govengine_decision_type = decision.decision_type.value
        operation.govengine_decision_summary = decision.summary

        received_event = self.evidence.emit(
            operation_id=operation.id,
            event_type=EvidenceEventType.GOVENGINE_DECISION_RECEIVED,
            correlation_id=correlation_id,
            state_before=operation.state,
            payload=decision.as_dict(),
        )
        operation.evidence_event_ids.append(received_event)
        return decision

    def _apply_governance_transition(
        self,
        operation: Operation,
        decision: GovEngineDecision,
        correlation_id: str,
    ) -> None:
        if decision.decision_type == GovEngineDecisionType.ALLOWED:
            self._transition(
                operation,
                OperationState.APPROVED,
                reason="govengine_allowed",
                correlation_id=correlation_id,
            )
            return

        if decision.decision_type in WAITING_DECISIONS:
            self._transition(
                operation,
                OperationState.WAITING_FOR_APPROVAL,
                reason=f"govengine_{decision.decision_type.value}",
                correlation_id=correlation_id,
            )
            return

        if decision.decision_type in BLOCKING_DECISIONS:
            self._transition(
                operation,
                OperationState.BLOCKED,
                reason=f"govengine_{decision.decision_type.value}",
                correlation_id=correlation_id,
            )
            return

        self._transition(
            operation,
            OperationState.BLOCKED,
            reason="govengine_unknown_decision",
            correlation_id=correlation_id,
        )

    def _transition(
        self,
        operation: Operation,
        target: OperationState,
        *,
        reason: str,
        correlation_id: str,
    ) -> None:
        current = operation.operation_state
        validate_transition(current, target)
        record = StateTransitionRecord(
            from_state=current.value,
            to_state=target.value,
            timestamp_utc=utc_now_iso(),
            reason=reason,
        )
        operation.history.append(record)
        operation.state = target.value
        operation.updated_at = record.timestamp_utc

        event_id = self.evidence.emit(
            operation_id=operation.id,
            event_type=EvidenceEventType.STATE_TRANSITION,
            correlation_id=correlation_id,
            state_before=current.value,
            state_after=target.value,
            payload={"reason": reason},
        )
        operation.evidence_event_ids.append(event_id)
