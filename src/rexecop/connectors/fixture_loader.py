from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from rexecop.connectors.base import ConnectorRuntime
from rexecop.secrets.port import SecretResolver

CONNECTOR_BACKEND_ENTRY_GROUP = "rexecop.connector_backends"


def _iter_connector_backend_entry_points() -> list:
    return list(entry_points(group=CONNECTOR_BACKEND_ENTRY_GROUP))


def list_registered_connector_backends() -> list[str]:
    return sorted(ep.name for ep in _iter_connector_backend_entry_points())


def load_connector_backend(name: str) -> ConnectorRuntime | None:
    """Load a zero-arg fixture backend (legacy mock fixtures)."""
    for ep in _iter_connector_backend_entry_points():
        if ep.name != name:
            continue
        loaded = ep.load()
        if isinstance(loaded, type):
            return loaded()
        if callable(loaded):
            runtime = loaded()
            if hasattr(runtime, "invoke"):
                return runtime  # type: ignore[no-any-return]
    return None


def load_connector_backend_for_connector(
    name: str,
    *,
    connector_name: str,
    config: dict[str, Any],
    profile_root: str | None,
    mutating_allowed: bool,
    secret_resolver: SecretResolver | None = None,
) -> ConnectorRuntime | None:
    """Load a domain connector backend factory registered via entry points."""
    for ep in _iter_connector_backend_entry_points():
        if ep.name != name:
            continue
        factory = ep.load()
        if not callable(factory):
            return None
        try:
            runtime = factory(
                connector_name=connector_name,
                config=config,
                profile_root=profile_root,
                mutating_allowed=mutating_allowed,
                secret_resolver=secret_resolver,
            )
        except TypeError:
            runtime = factory()
        if hasattr(runtime, "invoke"):
            return runtime  # type: ignore[no-any-return]
    return None
