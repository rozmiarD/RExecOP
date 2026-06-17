from __future__ import annotations

from pathlib import Path
from typing import Any

from rexecop.adapters.govengine_port.contracts import (
    BLOCKING_DECISIONS,
    WAITING_DECISIONS,
    is_mutating_mode,
)
from rexecop.errors import RExecOpStateError, RExecOpValidationError
from rexecop.escalation.package import build_escalation_package
from rexecop.evidence.event import EvidenceEventType
from rexecop.evidence.manager import EvidenceManager
from rexecop.execution.backend import StepExecutionResult
from rexecop.execution.executor import StepExecutor
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.operation.state import OperationState
from rexecop.profile.loader import LoadedProfile, load_profile
from rexecop.storage.file_store import FileStore
from rexecop.validation.validator import validate_operation_result
from rexecop.workflow.runner import WorkflowRunner

READ_ONLY_MODES = frozenset({"dry_run", "observe", "emergency_readonly"})
TERMINAL_STATES = frozenset(
    {
        OperationState.COMPLETED.value,
        OperationState.FAILED.value,
        OperationState.CANCELLED.value,
        OperationState.ESCALATED.value,
        OperationState.BLOCKED.value,
    }
)


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
        payload = result.as_dict()
        self._orchestrator._emit_step_event(
            self._operation,
            EvidenceEventType.STEP_COMPLETED,
            step_id=step_id,
            correlation_id=correlation_id,
            payload=payload,
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
        self._prepare_start(operation)
        return self._continue_execution(operation_id)

    def advance(self, operation_id: str, *, max_steps: int = 1) -> Operation:
        operation = self.store.load_operation(operation_id)
        if operation.state in {
            OperationState.APPROVED.value,
            OperationState.RESUMING.value,
            OperationState.RETRYING.value,
        }:
            self._prepare_start(operation)
        return self._continue_execution(operation_id, max_steps=max_steps)

    def pause(self, operation_id: str) -> Operation:
        operation = self.store.load_operation(operation_id)
        if operation.state != OperationState.RUNNING.value:
            raise RExecOpValidationError(
                f"pause requires running operation, got {operation.state}"
            )
        plan = self.store.load_plan(operation_id)
        current = operation.current_step_id
        if current not in plan.pause_safe_points:
            raise RExecOpValidationError(
                f"pause only allowed at pause_safe steps, current={current!r}"
            )
        operation.metadata["pause_requested"] = True
        self._transition(
            operation,
            OperationState.PAUSED,
            reason="operator_pause",
            correlation_id=operation.correlation_id,
        )
        cursor = self._execution_cursor(operation)
        cursor["paused_at_step_id"] = current
        operation.metadata["execution_cursor"] = cursor
        self.store.save_operation(operation)
        return operation

    def resume(self, operation_id: str) -> Operation:
        operation = self.store.load_operation(operation_id)
        if operation.state != OperationState.PAUSED.value:
            raise RExecOpValidationError(
                f"resume requires paused operation, got {operation.state}"
            )
        self._transition(
            operation,
            OperationState.RESUMING,
            reason="operator_resume",
            correlation_id=operation.correlation_id,
        )
        operation.metadata.pop("pause_requested", None)
        self.store.save_operation(operation)
        self._prepare_start(operation)
        return self._continue_execution(operation_id)

    def cancel(self, operation_id: str) -> Operation:
        operation = self.store.load_operation(operation_id)
        if operation.state not in {
            OperationState.WAITING_FOR_APPROVAL.value,
            OperationState.APPROVED.value,
            OperationState.RUNNING.value,
            OperationState.PAUSED.value,
        }:
            raise RExecOpValidationError(
                f"operation cannot be cancelled from state: {operation.state}"
            )
        self._transition(
            operation,
            OperationState.CANCELLED,
            reason="operator_cancel",
            correlation_id=operation.correlation_id,
        )
        self.store.save_operation(operation)
        return operation

    def retry(self, operation_id: str) -> Operation:
        operation = self.store.load_operation(operation_id)
        if operation.state != OperationState.FAILED.value:
            raise RExecOpValidationError(
                f"retry requires failed operation, got {operation.state}"
            )
        plan = self.store.load_plan(operation_id)
        failure = dict(operation.metadata.get("last_failure") or {})
        error_class = str(failure.get("error_class") or "")
        if not self._error_retryable(plan, error_class=error_class):
            raise RExecOpValidationError(
                f"retry not allowed for error_class={error_class!r}"
            )
        cursor = self._execution_cursor(operation)
        step_id = str(failure.get("step_id") or "")
        attempts_by_step = dict(cursor.get("attempts_by_step") or {})
        if step_id:
            attempts_by_step[step_id] = 0
        cursor["attempts_by_step"] = attempts_by_step
        operation.metadata["execution_cursor"] = cursor
        self._transition(
            operation,
            OperationState.RETRYING,
            reason="operator_retry",
            correlation_id=operation.correlation_id,
        )
        self.store.save_operation(operation)
        self._prepare_start(operation)
        return self._continue_execution(operation_id)

    def validate(self, operation_id: str) -> dict[str, Any]:
        operation = self.store.load_operation(operation_id)
        shared_state = dict(operation.metadata.get("shared_state") or {})
        return validate_operation_result(
            intent=operation.intent,
            shared_state=shared_state,
            profile=self._profile_for_operation(operation),
        )

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

    def _prepare_start(self, operation: Operation) -> None:
        plan = self.store.load_plan(operation.id)
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

        if operation.state == OperationState.RESUMING.value:
            self._transition(
                operation,
                OperationState.RUNNING,
                reason="resume_execution",
                correlation_id=correlation_id,
            )
        elif operation.state == OperationState.RETRYING.value:
            self._transition(
                operation,
                OperationState.RUNNING,
                reason="retry_execution",
                correlation_id=correlation_id,
            )
        elif operation.state == OperationState.APPROVED.value:
            self._transition(
                operation,
                OperationState.RUNNING,
                reason="execution_started",
                correlation_id=correlation_id,
            )
            self._init_execution_cursor(operation, plan)

        if operation.state in {
            OperationState.BLOCKED.value,
            OperationState.WAITING_FOR_APPROVAL.value,
            OperationState.CANCELLED.value,
        }:
            raise RExecOpValidationError(
                f"operation cannot be started from state: {operation.state}"
            )

        if operation.state != OperationState.RUNNING.value:
            raise RExecOpStateError(
                f"operation must be running to execute, got {operation.state}"
            )

        if is_mutating_mode(operation.mode) and not self._allows_mutating(operation):
            raise RExecOpValidationError(
                "mutating execution blocked until GovEngine allows and operation is approved"
            )

        self.store.save_operation(operation)

    def _continue_execution(
        self,
        operation_id: str,
        *,
        max_steps: int | None = None,
    ) -> Operation:
        steps_executed = 0
        while True:
            operation = self.store.load_operation(operation_id)
            if operation.state in TERMINAL_STATES or operation.state == OperationState.PAUSED.value:
                return operation

            if operation.state == OperationState.VALIDATING.value:
                return self._finalize_validation(operation)

            if operation.state != OperationState.RUNNING.value:
                return operation

            plan = self.store.load_plan(operation_id)
            cursor = self._execution_cursor(operation)
            start_index = int(cursor.get("next_step_index") or 0)
            if start_index >= len(plan.planned_steps):
                return self._begin_validation(operation)

            if operation.metadata.get("cancel_requested"):
                self._transition(
                    operation,
                    OperationState.CANCELLED,
                    reason="operator_cancel",
                    correlation_id=operation.correlation_id,
                )
                self.store.save_operation(operation)
                return operation

            shared_state = dict(operation.metadata.get("shared_state") or {})
            sink = _WorkflowEvidenceSink(self, operation)
            run_result = self.runner.run(
                operation_id=operation.id,
                target=operation.target,
                mode=operation.mode,
                planned_steps=plan.planned_steps,
                correlation_id=operation.correlation_id,
                evidence_sink=sink,
                shared_state=shared_state,
                start_index=start_index,
                max_steps=1,
            )
            operation.metadata["shared_state"] = run_result.shared_state
            operation.metadata["step_results"] = run_result.step_results
            cursor["next_step_index"] = run_result.next_step_index
            operation.metadata["execution_cursor"] = cursor
            self.store.save_operation(operation)

            if not run_result.success:
                return self._handle_step_failure(operation, plan, run_result)

            last_step_id = run_result.executed_steps[-1] if run_result.executed_steps else ""
            operation.current_step_id = last_step_id
            steps_executed += 1

            if operation.metadata.get("pause_requested") and last_step_id in plan.pause_safe_points:
                self._transition(
                    operation,
                    OperationState.PAUSED,
                    reason="operator_pause",
                    correlation_id=operation.correlation_id,
                )
                cursor["paused_at_step_id"] = last_step_id
                operation.metadata["execution_cursor"] = cursor
                self.store.save_operation(operation)
                return operation

            if run_result.next_step_index >= len(plan.planned_steps):
                operation = self.store.load_operation(operation_id)
                return self._begin_validation(operation)

            if max_steps is not None and steps_executed >= max_steps:
                return self.store.load_operation(operation_id)

    def _begin_validation(self, operation: Operation) -> Operation:
        self._transition(
            operation,
            OperationState.VALIDATING,
            reason="validation_started",
            correlation_id=operation.correlation_id,
        )
        self._emit_simple_event(
            operation,
            EvidenceEventType.VALIDATION_STARTED,
            correlation_id=operation.correlation_id,
        )
        self.store.save_operation(operation)
        return self._finalize_validation(operation)

    def _finalize_validation(self, operation: Operation) -> Operation:
        shared_state = dict(operation.metadata.get("shared_state") or {})
        validation = validate_operation_result(
            intent=operation.intent,
            shared_state=shared_state,
            profile=self._profile_for_operation(operation),
        )
        operation.metadata["validation"] = validation
        self._emit_simple_event(
            operation,
            EvidenceEventType.VALIDATION_COMPLETED,
            correlation_id=operation.correlation_id,
            payload=validation,
        )

        if validation.get("passed"):
            self._transition(
                operation,
                OperationState.COMPLETED,
                reason="validation_passed",
                correlation_id=operation.correlation_id,
            )
            self._emit_simple_event(
                operation,
                EvidenceEventType.OPERATION_COMPLETED,
                correlation_id=operation.correlation_id,
            )
        else:
            self._transition(
                operation,
                OperationState.FAILED,
                reason="validation_failed",
                correlation_id=operation.correlation_id,
            )
            self._emit_simple_event(
                operation,
                EvidenceEventType.OPERATION_FAILED,
                correlation_id=operation.correlation_id,
                payload=validation,
            )

        self.store.save_operation(operation)
        return operation

    def _handle_step_failure(
        self,
        operation: Operation,
        plan: OperationPlan,
        run_result: Any,
    ) -> Operation:
        step_id = run_result.stopped_step_id
        error_class = str(run_result.error_class or "unknown")
        cursor = self._execution_cursor(operation)
        attempts_by_step = dict(cursor.get("attempts_by_step") or {})
        attempts = int(attempts_by_step.get(step_id, 0)) + 1
        attempts_by_step[step_id] = attempts
        cursor["attempts_by_step"] = attempts_by_step
        operation.metadata["execution_cursor"] = cursor
        operation.metadata["last_failure"] = {
            "step_id": step_id,
            "error": run_result.error,
            "error_class": error_class,
            "attempt": attempts,
        }

        if self._can_retry(plan, error_class=error_class, attempts=attempts):
            self._transition(
                operation,
                OperationState.RETRYING,
                reason=f"retry_{error_class}",
                correlation_id=operation.correlation_id,
            )
            self.store.save_operation(operation)
            self._transition(
                operation,
                OperationState.RUNNING,
                reason="retry_execution",
                correlation_id=operation.correlation_id,
            )
            self.store.save_operation(operation)
            return self._continue_execution(operation.id)

        self._transition(
            operation,
            OperationState.FAILED,
            reason="step_execution_failed",
            correlation_id=operation.correlation_id,
        )
        self.store.save_operation(operation)
        return operation

    def _can_retry(
        self,
        plan: OperationPlan,
        *,
        error_class: str,
        attempts: int,
    ) -> bool:
        if not self._error_retryable(plan, error_class=error_class):
            return False
        max_attempts = int(plan.retry_policy_summary.get("max_attempts") or 0)
        return attempts <= max_attempts

    def _error_retryable(self, plan: OperationPlan, *, error_class: str) -> bool:
        policy = plan.retry_policy_summary
        blocked_on = [str(item) for item in policy.get("blocked_on") or []]
        allowed_on = [str(item) for item in policy.get("allowed_on") or []]
        if error_class in blocked_on:
            return False
        if allowed_on and error_class not in allowed_on:
            return False
        return bool(error_class)

    def _allows_mutating(self, operation: Operation) -> bool:
        if operation.govengine_decision_type in {item.value for item in BLOCKING_DECISIONS}:
            return False
        if operation.govengine_decision_type == "allowed":
            return True
        if operation.govengine_decision_type in {item.value for item in WAITING_DECISIONS}:
            approval_path = self.store.approvals_dir / f"{operation.id}.json"
            return approval_path.is_file()
        return False

    def _init_execution_cursor(self, operation: Operation, plan: OperationPlan) -> None:
        operation.metadata["execution_cursor"] = {
            "next_step_index": 0,
            "attempts_by_step": {},
            "paused_at_step_id": None,
            "pause_safe_points": list(plan.pause_safe_points),
        }

    def _profile_for_operation(self, operation: Operation) -> LoadedProfile | None:
        root = operation.metadata.get("profile_root")
        if not isinstance(root, str) or not root.strip():
            return None
        return load_profile(Path(root))

    def _execution_cursor(self, operation: Operation) -> dict[str, Any]:
        cursor = operation.metadata.get("execution_cursor")
        if not isinstance(cursor, dict):
            cursor = {"next_step_index": 0, "attempts_by_step": {}}
            operation.metadata["execution_cursor"] = cursor
        return cursor

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
