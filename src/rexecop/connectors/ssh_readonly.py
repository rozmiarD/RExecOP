from __future__ import annotations

import subprocess
from typing import Any

from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest, ConnectorResponse
from rexecop.connectors.command_shape import normalize_allowlisted_argv
from rexecop.connectors.errors import READ_ONLY_MODES
from rexecop.errors import RExecOpValidationError
from rexecop.evidence.redaction import redact_payload
from rexecop.secrets.port import SecretResolver
from rexecop.secrets.resolver import default_secret_resolver


class SshReadonlyRuntime:
    """Temporary read-only SSH connector — allowlisted remote commands only.

    Full policy integration belongs in GovEngine policy engine + SCLite review.
    Until that path exists, this backend is a documented temporary operator tool.
    """

    def __init__(
        self,
        *,
        connector_name: str,
        config: dict[str, Any],
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.connector_name = connector_name
        self.config = config
        self.secret_resolver = secret_resolver or default_secret_resolver()

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
                error="ssh_readonly refuses mutating operation modes",
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
        try:
            remote_command = self._build_remote_command(allowlist, entry)
            argv = self._build_ssh_argv(remote_command)
        except (RExecOpValidationError, ValueError) as exc:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error=str(exc),
                data={"error_class": connector_errors.VALIDATION_FAILED},
            )
        timeout = float(self.config.get("timeout_seconds") or 15)
        try:
            completed = subprocess.run(
                argv,
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
                error="ssh command timeout",
                data={"error_class": connector_errors.TIMEOUT},
            )
        success = completed.returncode == 0
        return ConnectorResponse(
            connector=request.connector,
            action=request.action,
            success=success,
            data=redact_payload(
                {
                    "stdout": completed.stdout.strip(),
                    "stderr": completed.stderr.strip(),
                    "returncode": completed.returncode,
                    "remote_command": remote_command,
                }
            ),
            error="" if success else completed.stderr.strip() or "ssh command failed",
        )

    def _build_remote_command(
        self,
        allowlist: list[Any],
        entry: dict[str, Any],
    ) -> str:
        allowed_tools = {
            str(item.get("command")).strip().lower()
            for item in allowlist
            if isinstance(item, dict) and str(item.get("command") or "").strip()
        }
        tool = str(entry.get("command") or "").strip()
        args = entry.get("args") or []
        if not isinstance(args, list):
            raise RExecOpValidationError("ssh allowlist args must be a list")
        argv = normalize_allowlisted_argv(tool=tool, args=args, allowed_tools=allowed_tools)
        return " ".join(argv)

    def _build_ssh_argv(self, remote_command: str) -> list[str]:
        host = str(self.config.get("host") or "").strip()
        user = str(self.config.get("user") or "").strip()
        if not host or not user:
            raise RExecOpValidationError("ssh_readonly requires host and user")
        argv = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
        ]
        port = self.config.get("port")
        if port is not None:
            argv.extend(["-p", str(port)])
        identity_ref = str(self.config.get("identity_file_secret_ref") or "").strip()
        if identity_ref:
            argv.extend(["-i", self.secret_resolver.resolve(identity_ref)])
        argv.append(f"{user}@{host}")
        argv.append(remote_command)
        return argv

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
