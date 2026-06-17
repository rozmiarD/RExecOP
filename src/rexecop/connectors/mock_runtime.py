from __future__ import annotations

from rexecop.connectors.base import ConnectorRequest, ConnectorResponse
from rexecop.connectors.mutating import MUTATING_ACTIONS


class MockConnectorRuntime:
    """Generic mock connector backend — no domain semantics."""

    _failure_counts: dict[tuple[str, str], dict[str, int | str]] = {}

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
        if failure and int(failure["remaining"]) > 0:
            failure["remaining"] = int(failure["remaining"]) - 1
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

        return ConnectorResponse(
            connector=request.connector,
            action=request.action,
            success=False,
            error=f"unsupported mock connector action: {request.connector}.{request.action}",
            data={"error_class": "unsupported"},
        )
