from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from rexecop.connectors.base import ConnectorRuntime
from rexecop.errors import RExecOpValidationError
from rexecop.plugins.contract import validate_connector_factory, validate_runtime_invoke
from rexecop.secrets.port import SecretResolver

CONNECTOR_BACKEND_ENTRY_GROUP = "rexecop.connector_backends"
RESERVED_CONNECTOR_BACKEND_NAMES = frozenset(
    {"mock", "http_api", "local_shell_readonly", "ssh_readonly", "static_fixture"}
)


def connector_backend_plugin_inventory() -> list[dict[str, Any]]:
    points = _iter_connector_backend_entry_points()
    counts = {name: sum(ep.name == name for ep in points) for name in {ep.name for ep in points}}
    return [
        {
            "name": ep.name,
            "entry_group": CONNECTOR_BACKEND_ENTRY_GROUP,
            "trusted_in_process": True,
            "contract": "rexecop.connector_backend_factory.v1",
            "name_collision": ep.name in RESERVED_CONNECTOR_BACKEND_NAMES or counts[ep.name] > 1,
        }
        for ep in points
    ]


def _iter_connector_backend_entry_points() -> list:
    return list(entry_points(group=CONNECTOR_BACKEND_ENTRY_GROUP))


def list_registered_connector_backends() -> list[str]:
    names = [ep.name for ep in _iter_connector_backend_entry_points()]
    collisions = sorted(set(names) & RESERVED_CONNECTOR_BACKEND_NAMES)
    if collisions:
        raise RExecOpValidationError(
            "plugin_name_collision: reserved connector backend: " + ",".join(collisions)
        )
    if len(names) != len(set(names)):
        raise RExecOpValidationError("plugin_name_collision: duplicate connector backend")
    return sorted(names)


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
    if name in RESERVED_CONNECTOR_BACKEND_NAMES:
        raise RExecOpValidationError(f"plugin_name_collision: reserved connector backend: {name}")
    if sum(ep.name == name for ep in _iter_connector_backend_entry_points()) > 1:
        raise RExecOpValidationError(f"plugin_name_collision: duplicate connector backend: {name}")
    for ep in _iter_connector_backend_entry_points():
        if ep.name != name:
            continue
        factory = ep.load()
        if not callable(factory):
            return None
        validate_connector_factory(factory)
        runtime = factory(
            connector_name=connector_name,
            config=config,
            profile_root=profile_root,
            mutating_allowed=mutating_allowed,
            secret_resolver=secret_resolver,
        )
        validate_runtime_invoke(runtime)
        return runtime  # type: ignore[no-any-return]
    return None
