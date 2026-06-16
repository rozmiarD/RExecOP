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
            return StepExecutionResult(
                step_id=step_id,
                success=False,
                output=response.as_dict(),
                error=response.error,
            )
        context.shared_state.setdefault("connector_results", {})[step_id] = response.data
        return StepExecutionResult(step_id=step_id, success=True, output=response.as_dict())

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
