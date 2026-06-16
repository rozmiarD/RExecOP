from __future__ import annotations

from typing import Any

from rexecop.connectors.base import ConnectorRequest, ConnectorResponse

MUTATING_ACTIONS = frozenset(
    {
        "restart",
        "delete",
        "create",
        "update",
        "apply",
        "stop",
        "start",
    }
)


class MockConnectorRuntime:
    """Fixture connector runtime for Phase 4+ vertical slices."""

    _failure_counts: dict[tuple[str, str], dict[str, Any]] = {}

    @classmethod
    def set_failures(
        cls,
        connector: str,
        action: str,
        *,
        count: int,
        error: str = "transient connector failure",
        error_class: str = "transient_connector_error",
    ) -> None:
        cls._failure_counts[(connector, action)] = {
            "remaining": count,
            "error": error,
            "error_class": error_class,
        }

    @classmethod
    def clear_failures(cls) -> None:
        cls._failure_counts.clear()

    def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
        failure = self._failure_counts.get((request.connector, request.action))
        if failure and failure["remaining"] > 0:
            failure["remaining"] -= 1
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error=str(failure["error"]),
                data={"error_class": str(failure["error_class"])},
            )

        if request.mode in {"dry_run", "observe", "emergency_readonly"}:
            if request.action in MUTATING_ACTIONS:
                return ConnectorResponse(
                    connector=request.connector,
                    action=request.action,
                    success=False,
                    error="mutating connector action refused in read-only mode",
                    data={"error_class": "policy_denied"},
                )

        if request.connector == "proxmox" and request.action == "list_vms":
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=True,
                data={
                    "vms": [
                        {"id": "vm-101", "name": "zabbix-proxy", "critical": True},
                        {"id": "vm-102", "name": "backup-gateway", "critical": True},
                    ]
                },
            )

        if request.connector == "proxmox" and request.action == "restart":
            before_state = {
                "vm_id": "vm-101",
                "agent_status": "running",
                "target": request.target,
            }
            after_state = {
                "vm_id": "vm-101",
                "agent_status": "restarted",
                "target": request.target,
            }
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=True,
                data={
                    "before_state": before_state,
                    "after_state": after_state,
                    "mutation": "restart_zabbix_agent",
                },
            )

        if request.connector == "pbs" and request.action == "list_snapshots":
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=True,
                data={
                    "snapshots": [
                        {"vm_id": "vm-101", "status": "ok"},
                        {"vm_id": "vm-102", "status": "ok"},
                    ]
                },
            )

        return ConnectorResponse(
            connector=request.connector,
            action=request.action,
            success=False,
            error=f"unsupported mock connector action: {request.connector}.{request.action}",
            data={"error_class": "unsupported"},
        )
