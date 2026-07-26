from __future__ import annotations

from typing import Any

from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import (
    ConnectorRequest,
    ConnectorResponse,
    effective_timeout_seconds,
)
from rexecop.connectors.command_shape import normalize_allowlisted_argv
from rexecop.connectors.errors import READ_ONLY_MODES
from rexecop.evidence.redaction import redact_payload, redact_text
from rexecop.execution import bounded_subprocess as capture_runtime

# Preserve the existing connector test seam while routing it to bounded capture.
subprocess = capture_runtime


class LocalShellReadonlyRuntime:
    """Strictly non-mutating allowlisted local shell commands."""

    def __init__(self, *, connector_name: str, config: dict[str, Any]) -> None:
        self.connector_name = connector_name
        self.config = config

    def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
        if request.connector != self.connector_name:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error="connector mismatch",
                data={"error_class": connector_errors.UNSUPPORTED},
            )
        if request.mode not in READ_ONLY_MODES:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error="local_shell_readonly refuses mutating operation modes",
                data={"error_class": connector_errors.POLICY_DENIED},
            )
        allowlist = self.config.get("allowlist")
        if not isinstance(allowlist, list):
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error="allowlist missing",
                data={"error_class": connector_errors.VALIDATION_FAILED},
            )
        entry = self._find_allowlist_entry(allowlist, request.action)
        if entry is None:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error="command not allowlisted",
                data={"error_class": connector_errors.CAPABILITY_UNDECLARED},
            )
        allowed_tools = {
            str(item.get("command")).strip().lower()
            for item in allowlist
            if isinstance(item, dict) and str(item.get("command") or "").strip()
        }
        tool = str(entry.get("command") or "").strip()
        args = entry.get("args") or []
        if not isinstance(args, list):
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error="allowlist args must be a list",
                data={"error_class": connector_errors.VALIDATION_FAILED},
            )
        try:
            command = normalize_allowlisted_argv(
                tool=tool,
                args=args,
                allowed_tools=allowed_tools,
            )
        except ValueError as exc:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error=str(exc),
                data={"error_class": connector_errors.VALIDATION_FAILED},
            )
        timeout = effective_timeout_seconds(
            request,
            float(self.config.get("timeout_seconds") or 10),
        )
        controls = request.metadata.get("execution_controls")
        try:
            configured_output_bytes = self.config.get("max_output_bytes", 65536)
            if isinstance(controls, dict) and "max_output_bytes" in controls:
                max_output_bytes = capture_runtime.resolve_output_limit(
                    configured=configured_output_bytes,
                    policy=controls["max_output_bytes"],
                )
            else:
                max_output_bytes = capture_runtime.resolve_output_limit(
                    configured=configured_output_bytes,
                )
        except ValueError:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error="invalid max_output_bytes",
                data={"error_class": connector_errors.VALIDATION_FAILED},
            )
        try:
            completed = capture_runtime.run(
                command,
                timeout=timeout,
                max_output_bytes=max_output_bytes,
            )
        except capture_runtime.TimeoutExpired:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error="local shell timeout",
                data={"error_class": connector_errors.TIMEOUT},
            )
        result = capture_runtime.normalize_result(
            completed,
            max_output_bytes=max_output_bytes,
        )
        success = result.returncode == 0 and not result.output_limit_exceeded
        data = {
            "stdout": result.stdout.text,
            "stderr": result.stderr.text,
            "returncode": result.returncode,
            "max_output_bytes": max_output_bytes,
            "output_limit_exceeded": result.output_limit_exceeded,
            "output_digests": {
                "stdout": result.stdout.digest,
                "stderr": result.stderr.digest,
            },
            "output_truncated": {
                "stdout": result.stdout.truncated,
                "stderr": result.stderr.truncated,
            },
            "output_sizes": {
                "stdout_bytes": result.stdout.total_bytes,
                "stderr_bytes": result.stderr.total_bytes,
                "total_bytes": result.stdout.total_bytes + result.stderr.total_bytes,
            },
        }
        if result.output_limit_exceeded:
            data["error_class"] = capture_runtime.OUTPUT_LIMIT_EXCEEDED
        return ConnectorResponse(
            connector=request.connector,
            action=request.action,
            success=success,
            data=redact_payload(data),
            error=(
                ""
                if success
                else "local shell output limit exceeded"
                if result.output_limit_exceeded
                else redact_text(result.stderr.text) or "command failed"
            ),
        )

    def _find_allowlist_entry(
        self,
        allowlist: list[Any],
        action: str,
    ) -> dict[str, Any] | None:
        for item in allowlist:
            if not isinstance(item, dict):
                continue
            if str(item.get("action") or item.get("command")) == action:
                return item
        return None
