from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib.metadata import entry_points
from typing import Any

from rexecop.execution.backend import StepExecutionContext

InternalHandler = Callable[[StepExecutionContext], dict[str, Any]]

INTERNAL_ACTION_ENTRY_GROUP = "rexecop.internal_actions"


def _builtin_handlers() -> dict[str, InternalHandler]:
    return {
        "record_rollback_marker": _record_rollback_marker,
    }


def _record_rollback_marker(context: StepExecutionContext) -> dict[str, Any]:
    marker = {
        "operation_id": context.operation_id,
        "target": context.target,
        "status": "rollback_recorded",
    }
    context.shared_state["rollback_marker"] = marker
    return marker


def _iter_internal_action_entry_points() -> list:
    return list(entry_points(group=INTERNAL_ACTION_ENTRY_GROUP))


def load_internal_handlers(
    *,
    extra: Mapping[str, InternalHandler] | None = None,
) -> dict[str, InternalHandler]:
    """Merge built-in handlers with rexecop.internal_actions entry points."""
    handlers = _builtin_handlers()
    for ep in _iter_internal_action_entry_points():
        loaded = ep.load()
        if callable(loaded):
            registered = loaded()
            if isinstance(registered, Mapping):
                handlers.update(registered)
    if extra:
        handlers.update(extra)
    return handlers


def list_registered_internal_actions() -> list[str]:
    names = set(_builtin_handlers())
    for ep in _iter_internal_action_entry_points():
        loaded = ep.load()
        if callable(loaded):
            registered = loaded()
            if isinstance(registered, Mapping):
                names.update(registered)
    return sorted(names)
