from __future__ import annotations

from pathlib import Path
from typing import Any

from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest, ConnectorResponse, ConnectorRuntime
from rexecop.connectors.fixture_loader import load_connector_backend
from rexecop.connectors.http_api import HttpApiConnectorRuntime
from rexecop.connectors.local_shell import LocalShellReadonlyRuntime
from rexecop.connectors.mock_runtime import MockConnectorRuntime
from rexecop.connectors.ssh_readonly import SshReadonlyRuntime
from rexecop.secrets.port import SecretResolver
from rexecop.secrets.resolver import default_secret_resolver


class CompositeConnectorRuntime:
    """Route connector calls to mock, http_api, or local_shell_readonly backends."""

    def __init__(
        self,
        *,
        connectors: dict[str, Any],
        profile_root: str | None,
        mutating_allowed: bool,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.connectors = connectors
        self.profile_root = profile_root
        self.mutating_allowed = mutating_allowed
        self.secret_resolver = secret_resolver or default_secret_resolver()
        self._mock = MockConnectorRuntime()
        self._backends: dict[str, ConnectorRuntime] = {}
        self._build_backends()

    def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
        config = self.connectors.get(request.connector)
        if not isinstance(config, dict):
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error=f"connector not configured: {request.connector}",
                data={"error_class": connector_errors.UNSUPPORTED},
            )
        if not bool(config.get("enabled", True)):
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error=f"connector disabled: {request.connector}",
                data={"error_class": connector_errors.CONNECTOR_DISABLED},
            )
        backend = self._backend_for(request.connector, config)
        return backend.invoke(request)

    def _build_backends(self) -> None:
        for name, config in self.connectors.items():
            if not isinstance(config, dict):
                continue
            self._backend_for(name, config)

    def _backend_for(self, name: str, config: dict[str, Any]) -> ConnectorRuntime:
        if name in self._backends:
            return self._backends[name]
        backend_name = str(config.get("backend") or config.get("mode") or "mock")
        if backend_name == "http_api":
            runtime: ConnectorRuntime = HttpApiConnectorRuntime(
                connector_name=name,
                config=config,
                profile_root=self.profile_root,
                mutating_allowed=self.mutating_allowed,
                secret_resolver=self.secret_resolver,
            )
        elif backend_name == "local_shell_readonly":
            runtime = LocalShellReadonlyRuntime(connector_name=name, config=config)
        elif backend_name == "ssh_readonly":
            runtime = SshReadonlyRuntime(
                connector_name=name,
                config=config,
                secret_resolver=self.secret_resolver,
            )
        else:
            fixture_name = str(config.get("fixture") or "").strip()
            fixture_runtime = load_connector_backend(fixture_name) if fixture_name else None
            runtime = fixture_runtime if fixture_runtime is not None else self._mock
        self._backends[name] = runtime
        return runtime


def build_connector_runtime(
    *,
    connectors: dict[str, Any],
    profile_root: str | Path | None,
    mutating_allowed: bool,
    secret_resolver: SecretResolver | None = None,
) -> CompositeConnectorRuntime:
    root = str(profile_root) if profile_root is not None else None
    return CompositeConnectorRuntime(
        connectors=connectors,
        profile_root=root,
        mutating_allowed=mutating_allowed,
        secret_resolver=secret_resolver,
    )
