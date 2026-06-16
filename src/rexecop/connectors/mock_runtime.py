from __future__ import annotations

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
    """Fixture connector runtime for Phase 4 vertical slice."""

    def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
        if request.mode in {"dry_run", "observe", "emergency_readonly"}:
            if request.action in MUTATING_ACTIONS:
                return ConnectorResponse(
                    connector=request.connector,
                    action=request.action,
                    success=False,
                    error="mutating connector action refused in read-only mode",
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
        )
