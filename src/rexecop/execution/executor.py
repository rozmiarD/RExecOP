from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.runtime import ConnectorDispatcher
from rexecop.execution.backend import StepExecutionContext, StepExecutionResult
from rexecop.execution.internal_registry import InternalHandler, load_internal_handlers

EvidenceHandler = Callable[[StepExecutionContext], dict[str, Any]]

__all__ = ["StepExecutor"]


class StepExecutor:
    def __init__(
        self,
        connector_dispatcher: ConnectorDispatcher | None = None,
        *,
        evidence_handler: EvidenceHandler | None = None,
        internal_handlers: Mapping[str, InternalHandler] | None = None,
    ) -> None:
        self.connector_dispatcher = connector_dispatcher or ConnectorDispatcher()
        self.evidence_handler = evidence_handler
        self._internal_handlers = load_internal_handlers(extra=internal_handlers)

    def execute(self, context: StepExecutionContext) -> StepExecutionResult:
        step_id = str(context.step.get("id") or "")
        step_type = str(context.step.get("type") or "internal")
        action = str(context.step.get("action") or "")

        try:
            if step_type == "connector":
                return self._execute_connector(context, step_id, action)
            if step_type == "evidence":
                return self._execute_evidence(context, step_id, action)
            return self._execute_internal(context, step_id, action)
        except Exception as exc:  # noqa: BLE001 - step boundary
            return StepExecutionResult(step_id=step_id, success=False, output={}, error=str(exc))

    def _execute_connector(
        self,
        context: StepExecutionContext,
        step_id: str,
        action: str,
    ) -> StepExecutionResult:
        connector = str(context.step.get("connector") or "")
        response = self.connector_dispatcher.invoke(
            ConnectorRequest(
                connector=connector,
                action=action,
                target=context.target,
                mode=context.mode,
            )
        )
        if not response.success:
            output = response.as_dict()
            error_class = str(response.data.get("error_class") or "")
            if error_class:
                output["error_class"] = error_class
            return StepExecutionResult(
                step_id=step_id,
                success=False,
                output=output,
                error=response.error,
            )
        output = response.as_dict()
        before_state = response.data.get("before_state")
        after_state = response.data.get("after_state")
        if isinstance(before_state, dict):
            output["before_state"] = before_state
        if isinstance(after_state, dict):
            output["after_state"] = after_state
        context.shared_state.setdefault("connector_results", {})[step_id] = response.data
        if isinstance(before_state, dict) and isinstance(after_state, dict):
            context.shared_state.setdefault("mutation_states", {})[step_id] = {
                "before_state": before_state,
                "after_state": after_state,
            }
        return StepExecutionResult(step_id=step_id, success=True, output=output)

    def _execute_internal(
        self,
        context: StepExecutionContext,
        step_id: str,
        action: str,
    ) -> StepExecutionResult:
        handler = self._internal_handlers.get(action)
        if handler is None:
            return StepExecutionResult(
                step_id=step_id,
                success=False,
                output={},
                error=f"internal_action_not_registered:{action}",
            )
        output = handler(context)
        context.shared_state.setdefault("internal_results", {})[step_id] = output
        return StepExecutionResult(step_id=step_id, success=True, output=output)

    def _execute_evidence(
        self,
        context: StepExecutionContext,
        step_id: str,
        action: str,
    ) -> StepExecutionResult:
        if action == "produce_receipt" and self.evidence_handler is not None:
            output = self.evidence_handler(context)
            return StepExecutionResult(step_id=step_id, success=True, output=output)
        return StepExecutionResult(
            step_id=step_id,
            success=True,
            output={"action": action, "status": "recorded"},
        )
