from __future__ import annotations

from typing import Any

from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest, ConnectorResponse
from rexecop.connectors.errors import READ_ONLY_MODES
from rexecop.evidence.redaction import redact_payload, redact_text


class StaticFixtureRuntime:
    """Deterministic no-I/O backend for neutral runtime contract tests."""

    _failure_counts: dict[tuple[str, str], dict[str, int | str]] = {}

    @classmethod
    def set_failures(
        cls,
        connector: str,
        action: str,
        *,
        count: int,
        error: str = "transient fixture failure",
        error_class: str = connector_errors.TRANSIENT,
    ) -> None:
        cls._failure_counts[(connector, action)] = {
            "remaining": count,
            "error": error,
            "error_class": error_class,
        }

    @classmethod
    def clear_failures(cls) -> None:
        cls._failure_counts.clear()

    def __init__(
        self,
        *,
        connector_name: str,
        config: dict[str, Any],
        mutating_allowed: bool,
    ) -> None:
        self.connector_name = connector_name
        self.config = config
        self.mutating_allowed = mutating_allowed

    def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
        failure = self._failure_counts.get((request.connector, request.action))
        if failure and int(failure["remaining"]) > 0:
            failure["remaining"] = int(failure["remaining"]) - 1
            return self._failure(
                request,
                str(failure["error"]),
                str(failure["error_class"]),
            )
        if request.connector != self.connector_name:
            return self._failure(request, "connector mismatch")
        if self.config.get("fixture_only") is not True:
            return self._failure(request, "static_fixture requires fixture_only: true")
        actions = self.config.get("actions")
        if not isinstance(actions, dict):
            return self._failure(request, "static_fixture actions mapping required")
        spec = actions.get(request.action)
        if not isinstance(spec, dict):
            return self._failure(request, "static_fixture action not configured")

        mutating = spec.get("mutating") is True
        if mutating and request.mode in READ_ONLY_MODES:
            return self._failure(
                request,
                "mutating fixture action refused in read-only mode",
                connector_errors.POLICY_DENIED,
            )
        if mutating and not self.mutating_allowed:
            return self._failure(
                request,
                "mutating fixture action blocked until GovEngine allows",
                connector_errors.POLICY_DENIED,
            )

        data = spec.get("data")
        if not isinstance(data, dict):
            return self._failure(request, "static_fixture action data mapping required")
        success = spec.get("success", True) is True
        error = redact_text(str(spec.get("error") or ""))
        return ConnectorResponse(
            connector=request.connector,
            action=request.action,
            success=success,
            data=redact_payload(data),
            error=error,
        )

    @staticmethod
    def _failure(
        request: ConnectorRequest,
        error: str,
        error_class: str = connector_errors.VALIDATION_FAILED,
    ) -> ConnectorResponse:
        return ConnectorResponse(
            connector=request.connector,
            action=request.action,
            success=False,
            error=error,
            data={"error_class": error_class},
        )
