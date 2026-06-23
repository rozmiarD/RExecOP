from __future__ import annotations

import subprocess
from typing import Any

from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import (
    ConnectorRequest,
    ConnectorResponse,
    effective_output_bytes,
    effective_timeout_seconds,
)
from rexecop.connectors.command_shape import normalize_allowlisted_argv
from rexecop.connectors.errors import READ_ONLY_MODES
from rexecop.evidence.redaction import redact_payload, redact_text
from rexecop.execution.output import bounded_text


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
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error="local shell timeout",
                data={"error_class": connector_errors.TIMEOUT},
            )
        success = completed.returncode == 0
        max_output_bytes = effective_output_bytes(
            request,
            int(self.config.get("max_output_bytes") or 65536),
        )
        stdout = bounded_text(completed.stdout, max_bytes=max_output_bytes)
        stderr = bounded_text(completed.stderr, max_bytes=max_output_bytes)
        return ConnectorResponse(
            connector=request.connector,
            action=request.action,
            success=success,
            data=redact_payload(
                {
                    "stdout": stdout.text,
                    "stderr": stderr.text,
                    "returncode": completed.returncode,
                    "output_digests": {
                        "stdout": stdout.digest,
                        "stderr": stderr.digest,
                    },
                    "output_truncated": {
                        "stdout": stdout.truncated,
                        "stderr": stderr.truncated,
                    },
                    "output_sizes": {
                        "stdout_bytes": stdout.original_bytes,
                        "stderr_bytes": stderr.original_bytes,
                    },
                }
            ),
            error="" if success else redact_text(completed.stderr.strip()) or "command failed",
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
