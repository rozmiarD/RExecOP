from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sclite.integrity import artifact_descriptor

from rexecop.adapters.govengine_port.adapter import default_govengine_adapter
from rexecop.adapters.govengine_port.contracts import (
    BLOCKING_DECISIONS,
    WAITING_DECISIONS,
    GovEngineAdapter,
    GovEngineDecision,
    GovEngineDecisionType,
    GovEngineRequest,
    is_mutating_mode,
)
from rexecop.adapters.sclite_port.contracts import SCLITE_ARTIFACT_AUTHORITY
from rexecop.environment.loader import load_environment
from rexecop.errors import RExecOpValidationError
from rexecop.evidence.event import EvidenceEventType
from rexecop.evidence.manager import EvidenceManager
from rexecop.operation.model import Operation, StateTransitionRecord, utc_now_iso
from rexecop.operation.plan import OperationPlan
from rexecop.operation.state import OperationState, validate_transition
from rexecop.orchestration.orchestrator import OperationOrchestrator
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
        from rexecop.adapters.sclite_port.emitter import SCLiteArtifactEmitter
        from rexecop.adapters.sclite_port.placeholder_emitter import PlaceholderSCLiteEmitter

        self.sclite_emitter = SCLiteArtifactEmitter()
        self.placeholder_sclite_emitter = PlaceholderSCLiteEmitter()
        from rexecop.runtime_ops.coordinator import RuntimeCoordinator
        from rexecop.runtime_ops.rollback import RollbackExecutor

        self.runtime = RuntimeCoordinator(self.store)
        self.rollback_executor = RollbackExecutor()
        self.orchestrator = OperationOrchestrator(
            store=self.store,
            evidence=self.evidence,
            transition=self._transition,
            export_receipt=self.export_receipt,
        )

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

        operation.metadata["runtime_policy"] = {
            "max_concurrent_operations": int(
                environment.safety.get("max_concurrent_operations") or 1
            ),
            "target_lock_enabled": bool(
                environment.safety.get("target_lock_enabled", True)
            ),
            "maintenance_windows": list(
                environment.safety.get("maintenance_windows") or []
            ),
        }

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

        self._emit_sclite_intent(operation, plan)

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
        if operation.govengine_decision_type in {
            item.value for item in BLOCKING_DECISIONS
        }:
            return False
        if operation.govengine_decision_type == GovEngineDecisionType.ALLOWED.value:
            return operation.state == OperationState.APPROVED.value
        if operation.govengine_decision_type in {item.value for item in WAITING_DECISIONS}:
            if operation.state != OperationState.APPROVED.value:
                return False
            approval_path = self.store.approvals_dir / f"{operation_id}.json"
            return approval_path.is_file()
        return False

    def _governance_allows_rollback(self, operation: Operation) -> bool:
        if operation.govengine_decision_type == GovEngineDecisionType.ALLOWED.value:
            return True
        if operation.metadata.get("manual_approval"):
            return True
        return False

    def get_operation(self, operation_id: str) -> Operation:
        return self.store.load_operation(operation_id)

    def export_placeholder_receipt(self, operation_id: str) -> dict[str, object]:
        operation = self.get_operation(operation_id)
        plan = self.store.load_plan(operation_id)
        events = self.store.list_evidence_events(operation_id)
        export = self.placeholder_sclite_emitter.export_operation_receipt(
            operation_id=operation_id,
            events=events,
            plan_summary={
                "profile": plan.profile,
                "intent": plan.intent,
                "target": plan.target,
                "mode": plan.mode,
            },
        )
        operation.sclite_refs = self.placeholder_sclite_emitter.build_sclite_refs(export)
        path = self.store.save_receipt_export(operation_id, export.as_dict())
        receipt_event = self.evidence.emit(
            operation_id=operation_id,
            event_type=EvidenceEventType.RECEIPT_GENERATED,
            correlation_id=operation.correlation_id,
            state_before=operation.state,
            state_after=operation.state,
            payload={
                "authority": export.authority,
                "emitter": export.emitter,
                "receipt_export_path": str(path),
            },
        )
        operation.evidence_event_ids.append(receipt_event)
        self.store.save_operation(operation)
        return {"export": export.as_dict(), "path": str(path), "sclite_refs": operation.sclite_refs}

    def export_receipt(self, operation_id: str) -> dict[str, object]:
        operation = self.get_operation(operation_id)
        plan = self.store.load_plan(operation_id)
        bundle_dir = self.store.operation_sclite_dir(operation_id)
        emission = self.sclite_emitter.emit_operation_bundle(
            operation=operation,
            plan=plan,
            bundle_dir=str(bundle_dir),
        )
        operation.sclite_refs = emission.sclite_refs
        export_summary = emission.as_dict()
        path = self.store.save_receipt_export(operation_id, export_summary)
        receipt_event = self.evidence.emit(
            operation_id=operation_id,
            event_type=EvidenceEventType.RECEIPT_GENERATED,
            correlation_id=operation.correlation_id,
            state_before=operation.state,
            state_after=operation.state,
            payload={
                "authority": SCLITE_ARTIFACT_AUTHORITY,
                "emitter": "sclite",
                "bundle_dir": emission.bundle_dir,
                "receipt_export_path": str(path),
            },
        )
        operation.evidence_event_ids.append(receipt_event)
        self.store.save_operation(operation)
        return {
            "export": export_summary,
            "bundle_dir": emission.bundle_dir,
            "path": str(path),
            "sclite_refs": operation.sclite_refs,
            "review_verdict": emission.review_record.get("verdict"),
        }

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

    def start(self, operation_id: str) -> Operation:
        return self._start_operation(operation_id, drain_queue=True)

    def process_queue(self) -> list[str]:
        return self._drain_queue()

    def rollback(self, operation_id: str) -> dict[str, object]:
        operation = self.get_operation(operation_id)
        plan = self.store.load_plan(operation_id)
        result = self.rollback_executor.execute(
            operation=operation,
            plan=plan,
            govengine_allows=self._governance_allows_rollback(operation),
        )
        operation.metadata["rollback"] = result
        self.store.save_operation(operation)
        return result

    def _start_operation(self, operation_id: str, *, drain_queue: bool) -> Operation:
        operation = self.get_operation(operation_id)
        if operation.state == OperationState.APPROVED.value and is_mutating_mode(operation.mode):
            self.runtime.check_maintenance_window(operation)
            if self.runtime.admit_for_execution(operation) == "queued":
                return self.get_operation(operation_id)
        result = self.orchestrator.start(operation_id)
        self._release_runtime_if_terminal(result)
        if drain_queue:
            self._drain_queue()
        return self.get_operation(operation_id)

    def _release_runtime_if_terminal(self, operation: Operation) -> None:
        if operation.state in {
            OperationState.COMPLETED.value,
            OperationState.FAILED.value,
            OperationState.CANCELLED.value,
            OperationState.ESCALATED.value,
        }:
            self.runtime.release_operation(operation)

    def _drain_queue(self) -> list[str]:
        started: list[str] = []
        while True:
            next_id = self.runtime.queue.peek()
            if not next_id:
                break
            candidate = self.get_operation(next_id)
            if candidate.state != OperationState.APPROVED.value:
                self.runtime.queue.remove(next_id)
                continue
            if self.runtime.admit_for_execution(candidate) != "admitted":
                break
            self._start_operation(next_id, drain_queue=False)
            started.append(next_id)
        return started

    def advance(self, operation_id: str, *, max_steps: int = 1) -> Operation:
        operation = self.get_operation(operation_id)
        if operation.state == OperationState.APPROVED.value and is_mutating_mode(operation.mode):
            self.runtime.check_maintenance_window(operation)
            if self.runtime.admit_for_execution(operation) == "queued":
                return self.get_operation(operation_id)
        result = self.orchestrator.advance(operation_id, max_steps=max_steps)
        self._release_runtime_if_terminal(result)
        return self.get_operation(operation_id)

    def approve(self, operation_id: str, *, approved_by: str = "operator") -> Operation:
        operation = self.get_operation(operation_id)
        if operation.state != OperationState.WAITING_FOR_APPROVAL.value:
            raise RExecOpValidationError(
                f"approve requires waiting_for_approval, got {operation.state}"
            )
        approval = {
            "operation_id": operation_id,
            "approved_by": approved_by,
            "approved_at": utc_now_iso(),
            "govengine_decision_type": operation.govengine_decision_type,
        }
        self.store.save_approval(operation_id, approval)
        operation.metadata["manual_approval"] = approval
        approval_event = self.evidence.emit(
            operation_id=operation_id,
            event_type=EvidenceEventType.APPROVAL_RECEIVED,
            correlation_id=operation.correlation_id,
            state_before=operation.state,
            state_after=OperationState.APPROVED.value,
            payload=approval,
        )
        operation.evidence_event_ids.append(approval_event)
        self._transition(
            operation,
            OperationState.APPROVED,
            reason="manual_approval",
            correlation_id=operation.correlation_id,
        )
        self.store.save_operation(operation)
        return operation

    def pause(self, operation_id: str) -> Operation:
        return self.orchestrator.pause(operation_id)

    def resume(self, operation_id: str) -> Operation:
        operation = self.get_operation(operation_id)
        if is_mutating_mode(operation.mode):
            self.runtime.check_maintenance_window(operation)
            if self.runtime.admit_for_execution(operation) == "queued":
                return self.get_operation(operation_id)
        result = self.orchestrator.resume(operation_id)
        self._release_runtime_if_terminal(result)
        self._drain_queue()
        return self.get_operation(operation_id)

    def cancel(self, operation_id: str) -> Operation:
        operation = self.get_operation(operation_id)
        result = self.orchestrator.cancel(operation_id)
        self.runtime.release_operation(operation)
        self.runtime.queue.remove(operation_id)
        self._drain_queue()
        return result

    def retry(self, operation_id: str) -> Operation:
        operation = self.get_operation(operation_id)
        if is_mutating_mode(operation.mode):
            self.runtime.check_maintenance_window(operation)
            if self.runtime.admit_for_execution(operation) == "queued":
                return self.get_operation(operation_id)
        result = self.orchestrator.retry(operation_id)
        self._release_runtime_if_terminal(result)
        self._drain_queue()
        return self.get_operation(operation_id)

    def validate(self, operation_id: str) -> dict[str, object]:
        return self.orchestrator.validate(operation_id)

    def escalate(self, operation_id: str) -> dict[str, object]:
        return self.orchestrator.escalate(operation_id)

    def _emit_sclite_intent(self, operation: Operation, plan: OperationPlan) -> None:
        intent = self.sclite_emitter.emit_intent_contract(operation, plan)
        bundle_dir = self.store.operation_sclite_dir(operation.id)
        intent_path = bundle_dir / "01_intent_contract.json"
        intent_path.write_text(
            json.dumps(intent, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        descriptor = artifact_descriptor(intent)
        operation.sclite_refs = {
            **operation.sclite_refs,
            "intent_contract": {
                "sclite_schema_ref": descriptor["schema_ref"],
                "descriptor_path": str(intent_path),
                "digest": descriptor["digest"],
                "status": "emitted",
            },
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
        operation.metadata["govengine_admission"] = decision.as_dict()

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
