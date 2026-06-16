from __future__ import annotations

from typing import Any

from rexecop.adapters.govengine_port.contracts import is_mutating_mode
from rexecop.errors import RExecOpStateError, RExecOpValidationError
from rexecop.escalation.package import build_escalation_package
from rexecop.evidence.event import EvidenceEventType
from rexecop.evidence.manager import EvidenceManager
from rexecop.execution.backend import StepExecutionResult
from rexecop.execution.executor import StepExecutor
from rexecop.operation.model import Operation
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore
from rexecop.validation.validator import validate_operation_result
from rexecop.workflow.runner import WorkflowRunner

READ_ONLY_MODES = frozenset({"dry_run", "observe", "emergency_readonly"})


class _WorkflowEvidenceSink:
    def __init__(self, orchestrator: OperationOrchestrator, operation: Operation) -> None:
        self._orchestrator = orchestrator
        self._operation = operation

    def on_step_started(self, *, step_id: str, correlation_id: str) -> None:
        self._operation.current_step_id = step_id
        self._orchestrator._emit_step_event(
            self._operation,
            EvidenceEventType.STEP_STARTED,
            step_id=step_id,
            correlation_id=correlation_id,
            payload={"step_id": step_id},
        )

    def on_step_completed(
        self,
        *,
        step_id: str,
        result: StepExecutionResult,
        correlation_id: str,
    ) -> None:
        self._orchestrator._emit_step_event(
            self._operation,
            EvidenceEventType.STEP_COMPLETED,
            step_id=step_id,
            correlation_id=correlation_id,
            payload=result.as_dict(),
        )

    def on_step_failed(
        self,
        *,
        step_id: str,
        result: StepExecutionResult,
        correlation_id: str,
    ) -> None:
        self._orchestrator._emit_step_event(
            self._operation,
            EvidenceEventType.STEP_FAILED,
            step_id=step_id,
            correlation_id=correlation_id,
            payload=result.as_dict(),
        )


class OperationOrchestrator:
    def __init__(
        self,
        *,
        store: FileStore,
        evidence: EvidenceManager,
        transition: Any,
        export_receipt: Any,
    ) -> None:
        self.store = store
        self.evidence = evidence
        self._transition = transition
        self._export_receipt = export_receipt
        self.runner = WorkflowRunner(
            StepExecutor(evidence_handler=lambda ctx: self._export_receipt(ctx.operation_id))
        )

    def start(self, operation_id: str) -> Operation:
        operation = self.store.load_operation(operation_id)
        plan = self.store.load_plan(operation_id)
        correlation_id = operation.correlation_id

        if operation.state == OperationState.PLANNED.value and operation.mode in READ_ONLY_MODES:
            self._transition(
                operation,
                OperationState.APPROVED,
                reason="read_only_auto_approved",
                correlation_id=correlation_id,
            )
        elif operation.state == OperationState.PLANNED.value and is_mutating_mode(operation.mode):
            raise RExecOpValidationError(
                "mutating operation must be approved before start"
            )

        if operation.state != OperationState.APPROVED.value:
            raise RExecOpStateError(
                f"operation must be approved before start, got {operation.state}"
            )

        self._transition(
            operation,
            OperationState.RUNNING,
            reason="execution_started",
            correlation_id=correlation_id,
        )

        sink = _WorkflowEvidenceSink(self, operation)
        run_result = self.runner.run(
            operation_id=operation.id,
            target=operation.target,
            mode=operation.mode,
            planned_steps=plan.planned_steps,
            correlation_id=correlation_id,
            evidence_sink=sink,
            shared_state=dict(operation.metadata.get("shared_state") or {}),
        )
        operation.metadata["shared_state"] = run_result.shared_state
        operation.metadata["step_results"] = run_result.step_results

        if not run_result.success:
            self._transition(
                operation,
                OperationState.FAILED,
                reason="step_execution_failed",
                correlation_id=correlation_id,
            )
            self.store.save_operation(operation)
            return operation

        self._transition(
            operation,
            OperationState.VALIDATING,
            reason="validation_started",
            correlation_id=correlation_id,
        )
        self._emit_simple_event(
            operation,
            EvidenceEventType.VALIDATION_STARTED,
            correlation_id=correlation_id,
        )
        validation = validate_operation_result(
            intent=operation.intent,
            shared_state=run_result.shared_state,
        )
        operation.metadata["validation"] = validation
        self._emit_simple_event(
            operation,
            EvidenceEventType.VALIDATION_COMPLETED,
            correlation_id=correlation_id,
            payload=validation,
        )

        if validation.get("passed"):
            self._transition(
                operation,
                OperationState.COMPLETED,
                reason="validation_passed",
                correlation_id=correlation_id,
            )
            self._emit_simple_event(
                operation,
                EvidenceEventType.OPERATION_COMPLETED,
                correlation_id=correlation_id,
            )
        else:
            self._transition(
                operation,
                OperationState.FAILED,
                reason="validation_failed",
                correlation_id=correlation_id,
            )
            self._emit_simple_event(
                operation,
                EvidenceEventType.OPERATION_FAILED,
                correlation_id=correlation_id,
                payload=validation,
            )

        self.store.save_operation(operation)
        return operation

    def validate(self, operation_id: str) -> dict[str, Any]:
        operation = self.store.load_operation(operation_id)
        shared_state = dict(operation.metadata.get("shared_state") or {})
        return validate_operation_result(intent=operation.intent, shared_state=shared_state)

    def escalate(self, operation_id: str) -> dict[str, Any]:
        operation = self.store.load_operation(operation_id)
        if operation.state not in {OperationState.FAILED.value, OperationState.BLOCKED.value}:
            raise RExecOpValidationError(
                f"operation not escalatable from state: {operation.state}"
            )
        package = build_escalation_package(operation=operation, store=self.store)
        self._transition(
            operation,
            OperationState.ESCALATED,
            reason="operator_escalation",
            correlation_id=operation.correlation_id,
        )
        self._emit_simple_event(
            operation,
            EvidenceEventType.OPERATION_ESCALATED,
            correlation_id=operation.correlation_id,
            payload={"package": package},
        )
        operation.metadata["escalation_package"] = package
        self.store.save_operation(operation)
        return package

    def _emit_step_event(
        self,
        operation: Operation,
        event_type: EvidenceEventType,
        *,
        step_id: str,
        correlation_id: str,
        payload: dict[str, Any],
    ) -> None:
        event_id = self.evidence.emit(
            operation_id=operation.id,
            event_type=event_type,
            correlation_id=correlation_id,
            state_before=operation.state,
            state_after=operation.state,
            step_id=step_id,
            payload=payload,
        )
        operation.evidence_event_ids.append(event_id)
        self.store.save_operation(operation)

    def _emit_simple_event(
        self,
        operation: Operation,
        event_type: EvidenceEventType,
        *,
        correlation_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        event_id = self.evidence.emit(
            operation_id=operation.id,
            event_type=event_type,
            correlation_id=correlation_id,
            state_before=operation.state,
            state_after=operation.state,
            payload=payload or {},
        )
        operation.evidence_event_ids.append(event_id)
