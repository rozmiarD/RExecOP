from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib.metadata import entry_points
from typing import Any

from rexecop.errors import RExecOpValidationError
from rexecop.execution.backend import StepExecutionContext
from rexecop.plugins.contract import validate_internal_registrar

InternalHandler = Callable[[StepExecutionContext], dict[str, Any]]

INTERNAL_ACTION_ENTRY_GROUP = "rexecop.internal_actions"


def _builtin_handlers() -> dict[str, InternalHandler]:
    return {
        "record_execution_checkpoint": _record_execution_checkpoint,
        "record_rollback_marker": _record_rollback_marker,
    }


def _record_execution_checkpoint(context: StepExecutionContext) -> dict[str, Any]:
    checkpoint = {
        "step_id": str(context.step.get("id") or ""),
        "operation_id": context.operation_id,
        "status": "checkpoint_recorded",
    }
    context.shared_state.setdefault("execution_checkpoints", []).append(checkpoint)
    return checkpoint


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


def internal_action_plugin_inventory() -> list[dict[str, Any]]:
    return [
        {
            "name": ep.name,
            "entry_group": INTERNAL_ACTION_ENTRY_GROUP,
            "trusted_in_process": True,
            "contract": "rexecop.internal_action_registrar.v1",
        }
        for ep in _iter_internal_action_entry_points()
    ]


def load_internal_handlers(
    *,
    extra: Mapping[str, InternalHandler] | None = None,
) -> dict[str, InternalHandler]:
    """Merge built-in handlers with rexecop.internal_actions entry points."""
    handlers = _builtin_handlers()
    for ep in _iter_internal_action_entry_points():
        loaded = ep.load()
        if callable(loaded):
            validate_internal_registrar(loaded)
            registered = loaded()
            if isinstance(registered, Mapping):
                collisions = sorted(set(registered) & set(handlers))
                if collisions:
                    raise RExecOpValidationError(
                        "plugin_name_collision: internal action: " + ",".join(collisions)
                    )
                handlers.update(registered)
    if extra:
        collisions = sorted(set(extra) & set(handlers))
        if collisions:
            raise RExecOpValidationError(
                "plugin_name_collision: internal action: " + ",".join(collisions)
            )
        handlers.update(extra)
    return handlers


def list_registered_internal_actions() -> list[str]:
    names = set(_builtin_handlers())
    for ep in _iter_internal_action_entry_points():
        loaded = ep.load()
        if callable(loaded):
            validate_internal_registrar(loaded)
            registered = loaded()
            if isinstance(registered, Mapping):
                collisions = sorted(set(registered) & names)
                if collisions:
                    raise RExecOpValidationError(
                        "plugin_name_collision: internal action: " + ",".join(collisions)
                    )
                names.update(registered)
    return sorted(names)
