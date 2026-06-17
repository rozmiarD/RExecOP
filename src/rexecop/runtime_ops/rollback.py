from __future__ import annotations

from rexecop.connectors.composite_runtime import build_connector_runtime
from rexecop.connectors.runtime import ConnectorDispatcher
from rexecop.errors import RExecOpValidationError
from rexecop.execution.executor import StepExecutor
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.operation.state import OperationState
from rexecop.workflow.runner import WorkflowRunner


class RollbackExecutor:
    """Execute explicit workflow rollback steps when governance already allowed the op."""

    def can_execute(self, operation: Operation, plan: OperationPlan) -> bool:
        if operation.state != OperationState.FAILED.value:
            return False
        if not plan.rollback_available:
            return False
        rollback = plan.workflow.get("rollback")
        if not isinstance(rollback, dict):
            return False
        steps = rollback.get("steps")
        return isinstance(steps, list) and bool(steps)

    def execute(
        self,
        *,
        operation: Operation,
        plan: OperationPlan,
        govengine_allows: bool,
    ) -> dict[str, object]:
        from rexecop.adapters.govengine_port.contracts import is_mutating_mode

        if not govengine_allows:
            raise RExecOpValidationError("rollback blocked until GovEngine allows the operation")
        if not self.can_execute(operation, plan):
            raise RExecOpValidationError("rollback not defined for this failed operation")
        rollback = plan.workflow["rollback"]
        steps = [dict(step) for step in rollback.get("steps") or [] if isinstance(step, dict)]
        mode = str(rollback.get("mode") or "dry_run")
        if is_mutating_mode(mode) and not govengine_allows:
            raise RExecOpValidationError("mutating rollback requires GovEngine allow")
        shared_state = dict(operation.metadata.get("shared_state") or {})
        connectors = operation.metadata.get("environment_connectors")
        if not isinstance(connectors, dict):
            connectors = {}
        runtime = build_connector_runtime(
            connectors=connectors,
            profile_root=operation.metadata.get("profile_root"),
            mutating_allowed=govengine_allows,
        )
        runner = WorkflowRunner(
            StepExecutor(connector_dispatcher=ConnectorDispatcher(runtime))
        )
        result = runner.run(
            operation_id=operation.id,
            target=operation.target,
            mode=mode,
            planned_steps=steps,
            correlation_id=operation.correlation_id,
            shared_state=shared_state,
        )
        return {
            "mode": mode,
            "success": result.success,
            "executed_steps": result.executed_steps,
            "step_results": result.step_results,
            "error": result.error,
        }
