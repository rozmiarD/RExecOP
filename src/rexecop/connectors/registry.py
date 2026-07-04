from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rexecop import __version__
from rexecop.connectors.fixture_loader import list_registered_connector_backends

CONNECTOR_BACKEND_DESCRIPTOR_SCHEMA = "rexecop.connector_backend_descriptor.v0.1"
READ_ONLY_OPERATION_MODES = ("dry_run", "observe", "emergency_readonly", "read_only")
MUTATING_OPERATION_MODES = ("apply",)


@dataclass(frozen=True)
class ConnectorBackendDescriptor:
    backend_class: str
    source: str
    supported_modes: tuple[str, ...]
    capability_descriptors: tuple[str, ...]
    certification_tier: str
    compatibility_version: str
    identity_class: str = "none"
    egress_class: str = "no_network"
    read_only_backend: bool = False
    live_backend_capable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend_class": self.backend_class,
            "source": self.source,
            "supported_modes": list(self.supported_modes),
            "capability_descriptors": list(self.capability_descriptors),
            "certification_tier": self.certification_tier,
            "compatibility_version": self.compatibility_version,
            "identity_class": self.identity_class,
            "egress_class": self.egress_class,
            "read_only_backend": self.read_only_backend,
            "live_backend_capable": self.live_backend_capable,
        }


_BUILTIN_BACKENDS: dict[str, ConnectorBackendDescriptor] = {
    "mock": ConnectorBackendDescriptor(
        backend_class="mock",
        source="rexecop.core",
        supported_modes=READ_ONLY_OPERATION_MODES + MUTATING_OPERATION_MODES,
        capability_descriptors=("connector.mock.invoke",),
        certification_tier="bootstrap",
        compatibility_version=__version__,
        identity_class="none",
        egress_class="no_network",
        live_backend_capable=False,
    ),
    "http_api": ConnectorBackendDescriptor(
        backend_class="http_api",
        source="rexecop.core",
        supported_modes=READ_ONLY_OPERATION_MODES + MUTATING_OPERATION_MODES,
        capability_descriptors=("connector.http.rest.read", "connector.http.rest.mutate"),
        certification_tier="core",
        compatibility_version=__version__,
        identity_class="api_token_optional",
        egress_class="outbound_http",
        live_backend_capable=True,
    ),
    "local_shell_readonly": ConnectorBackendDescriptor(
        backend_class="local_shell_readonly",
        source="rexecop.core",
        supported_modes=READ_ONLY_OPERATION_MODES,
        capability_descriptors=("connector.shell.readonly",),
        certification_tier="core",
        compatibility_version=__version__,
        identity_class="none",
        egress_class="local_subprocess",
        read_only_backend=True,
        live_backend_capable=True,
    ),
    "ssh_readonly": ConnectorBackendDescriptor(
        backend_class="ssh_readonly",
        source="rexecop.core",
        supported_modes=READ_ONLY_OPERATION_MODES,
        capability_descriptors=("connector.ssh.readonly",),
        certification_tier="core",
        compatibility_version=__version__,
        identity_class="ssh_identity",
        egress_class="outbound_ssh",
        read_only_backend=True,
        live_backend_capable=True,
    ),
    "static_fixture": ConnectorBackendDescriptor(
        backend_class="static_fixture",
        source="rexecop.core",
        supported_modes=READ_ONLY_OPERATION_MODES + MUTATING_OPERATION_MODES,
        capability_descriptors=("connector.fixture.static",),
        certification_tier="core",
        compatibility_version=__version__,
        identity_class="none",
        egress_class="no_network",
        live_backend_capable=False,
    ),
}


def list_builtin_connector_backends() -> list[str]:
    return sorted(_BUILTIN_BACKENDS)


def describe_connector_backend(name: str) -> ConnectorBackendDescriptor:
    builtin = _BUILTIN_BACKENDS.get(name)
    if builtin is not None:
        return builtin
    if name not in list_registered_connector_backends():
        raise KeyError(name)
    return ConnectorBackendDescriptor(
        backend_class=name,
        source="rexecop.connector_backends",
        supported_modes=READ_ONLY_OPERATION_MODES + MUTATING_OPERATION_MODES,
        capability_descriptors=(f"connector.plugin.{name}",),
        certification_tier="plugin",
        compatibility_version=__version__,
        identity_class="plugin_declared",
        egress_class="plugin_undeclared",
        live_backend_capable=True,
    )


def list_connector_backend_names() -> list[str]:
    names = set(_BUILTIN_BACKENDS)
    names.update(list_registered_connector_backends())
    return sorted(names)


def list_connector_backend_descriptors() -> list[ConnectorBackendDescriptor]:
    return [describe_connector_backend(name) for name in list_connector_backend_names()]