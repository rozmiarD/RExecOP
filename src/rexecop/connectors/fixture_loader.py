from __future__ import annotations

from importlib.metadata import entry_points

from rexecop.connectors.base import ConnectorRuntime

CONNECTOR_BACKEND_ENTRY_GROUP = "rexecop.connector_backends"


def _iter_connector_backend_entry_points() -> list:
    return list(entry_points(group=CONNECTOR_BACKEND_ENTRY_GROUP))


def list_registered_connector_backends() -> list[str]:
    return sorted(ep.name for ep in _iter_connector_backend_entry_points())


def load_connector_backend(name: str) -> ConnectorRuntime | None:
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
