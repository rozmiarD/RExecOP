from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.runtime import ConnectorDispatcher
from rexecop.execution.backend import StepExecutionContext, StepExecutionResult

InternalHandler = Callable[[StepExecutionContext], dict[str, Any]]
EvidenceHandler = Callable[[StepExecutionContext], dict[str, Any]]


class StepExecutor:
    def __init__(
        self,
        connector_dispatcher: ConnectorDispatcher | None = None,
        *,
        evidence_handler: EvidenceHandler | None = None,
    ) -> None:
        self.connector_dispatcher = connector_dispatcher or ConnectorDispatcher()
        self.evidence_handler = evidence_handler
        self._internal_handlers: dict[str, InternalHandler] = {
            "environment.resolve_targets": self._resolve_targets,
            "correlate_vm_backup_coverage": self._correlate_coverage,
            "capture_agent_state": self._capture_agent_state,
            "verify_agent_state": self._verify_agent_state,
            "record_rollback_marker": self._record_rollback_marker,
        }

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
                error=f"unsupported internal action: {action}",
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

    def _resolve_targets(self, context: StepExecutionContext) -> dict[str, Any]:
        return {
            "target": context.target,
            "resolved_targets": ["vm-101", "vm-102"],
        }

    def _correlate_coverage(self, context: StepExecutionContext) -> dict[str, Any]:
        connector_results = context.shared_state.get("connector_results", {})
        vms = []
        snapshots = []
        for payload in connector_results.values():
            vms.extend(payload.get("vms", []))
            snapshots.extend(payload.get("snapshots", []))
        covered = {item.get("vm_id") for item in snapshots}
        rows = []
        for vm in vms:
            vm_id = vm.get("id")
            rows.append(
                {
                    "vm_id": vm_id,
                    "name": vm.get("name"),
                    "backup_status": "ok" if vm_id in covered else "missing",
                }
            )
        result = {
            "rows": rows,
            "all_critical_covered": all(row["backup_status"] == "ok" for row in rows),
        }
        context.shared_state["correlation"] = result
        return result

    def _capture_agent_state(self, context: StepExecutionContext) -> dict[str, Any]:
        state = {
            "target": context.target,
            "agent_status": "running",
            "vm_id": "vm-101",
        }
        context.shared_state["agent_before_state"] = dict(state)
        return {"before_state": state}

    def _verify_agent_state(self, context: StepExecutionContext) -> dict[str, Any]:
        mutation = context.shared_state.get("mutation_states", {}).get("restart_agent", {})
        after_state = mutation.get("after_state")
        if not isinstance(after_state, dict):
            return {
                "verified": False,
                "reason": "missing restart mutation after_state",
            }
        context.shared_state["agent_after_state"] = dict(after_state)
        return {
            "verified": after_state.get("agent_status") == "restarted",
            "after_state": after_state,
            "before_state": context.shared_state.get("agent_before_state"),
        }

    def _record_rollback_marker(self, context: StepExecutionContext) -> dict[str, Any]:
        marker = {
            "operation_id": context.operation_id,
            "target": context.target,
            "status": "rollback_recorded",
        }
        context.shared_state["rollback_marker"] = marker
        return marker
