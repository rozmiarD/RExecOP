from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib.metadata import entry_points
from itertools import islice
from typing import Any

from rexecop.errors import RExecOpValidationError
from rexecop.execution.backend import StepExecutionContext
from rexecop.plugins.contract import validate_internal_registrar
from rexecop.plugins.diagnostic_identity import (
    ProjectedDiagnosticIdentity,
    project_diagnostic_identity,
)

InternalHandler = Callable[[StepExecutionContext], dict[str, Any]]

INTERNAL_ACTION_ENTRY_GROUP = "rexecop.internal_actions"
_DIAGNOSTIC_INVENTORY_LIMIT = 64
_DIAGNOSTIC_ACTION_LIMIT = 64


@dataclass(frozen=True, slots=True)
class _InternalPluginDiagnostic:
    identity: ProjectedDiagnosticIdentity
    distribution: ProjectedDiagnosticIdentity
    status: str
    error_codes: tuple[str, ...] = ()
    registered_actions: tuple[ProjectedDiagnosticIdentity, ...] = ()
    conflicting_actions: tuple[ProjectedDiagnosticIdentity, ...] = ()
    exception_class: ProjectedDiagnosticIdentity | None = None


@dataclass(frozen=True, slots=True)
class _InternalPluginSnapshot:
    diagnostics: tuple[_InternalPluginDiagnostic, ...]
    installed_entry_points: tuple[ProjectedDiagnosticIdentity, ...]


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
    """Return a bounded diagnostic snapshot without weakening runtime loading."""
    return _serialize_internal_action_plugin_inventory(_snapshot_internal_action_plugins())


def _snapshot_internal_action_plugins() -> _InternalPluginSnapshot:
    try:
        points = _iter_internal_action_entry_points()
    except Exception as exc:  # noqa: BLE001 - diagnostic boundary
        return _InternalPluginSnapshot(
            diagnostics=(
                _InternalPluginDiagnostic(
                    identity=project_diagnostic_identity("unknown", kind="entry"),
                    distribution=project_diagnostic_identity(
                        "unknown", kind="distribution"
                    ),
                    status="failed",
                    error_codes=("entry_point_enumeration_failed",),
                    exception_class=_project_exception_class(exc),
                ),
            ),
            installed_entry_points=(),
        )

    diagnostics: list[_InternalPluginDiagnostic] = []
    installed_entry_points: list[ProjectedDiagnosticIdentity] = []
    registered_names = set(_builtin_handlers())
    inventory_overflow = len(points) > _DIAGNOSTIC_INVENTORY_LIMIT
    visible_limit = (
        _DIAGNOSTIC_INVENTORY_LIMIT - 1 if inventory_overflow else _DIAGNOSTIC_INVENTORY_LIMIT
    )
    for index, ep in enumerate(points):
        diagnostic = _inspect_internal_action_entry_point(
            ep,
            registered_names=registered_names,
        )
        installed_entry_points.append(diagnostic.identity)
        if index < visible_limit:
            diagnostics.append(diagnostic)
    if inventory_overflow:
        diagnostics.append(
            _InternalPluginDiagnostic(
                identity=project_diagnostic_identity("inventory_limit", kind="entry"),
                distribution=project_diagnostic_identity("unknown", kind="distribution"),
                status="failed",
                error_codes=("entry_point_inventory_limit_exceeded",),
            )
        )
    return _InternalPluginSnapshot(
        diagnostics=tuple(diagnostics),
        installed_entry_points=tuple(installed_entry_points),
    )


def _inspect_internal_action_entry_point(
    ep: Any,
    *,
    registered_names: set[Any],
) -> _InternalPluginDiagnostic:
    distribution = _entry_point_distribution_identity(ep)
    try:
        raw_name = ep.name
    except Exception as exc:  # noqa: BLE001 - untrusted entry-point metadata
        return _InternalPluginDiagnostic(
            identity=project_diagnostic_identity("unknown", kind="entry"),
            distribution=distribution,
            status="failed",
            error_codes=("entry_point_name_failed",),
            exception_class=_project_exception_class(exc),
        )
    identity = project_diagnostic_identity(raw_name, kind="entry")
    try:
        loaded = ep.load()
    except Exception as exc:  # noqa: BLE001 - diagnostic boundary
        return _InternalPluginDiagnostic(
            identity=identity,
            distribution=distribution,
            status="failed",
            error_codes=("entry_point_load_failed",),
            exception_class=_project_exception_class(exc),
        )
    if not callable(loaded):
        return _InternalPluginDiagnostic(
            identity=identity,
            distribution=distribution,
            status="failed",
            error_codes=("registrar_not_callable",),
        )
    try:
        validate_internal_registrar(loaded)
    except Exception as exc:  # noqa: BLE001 - diagnostic boundary
        return _InternalPluginDiagnostic(
            identity=identity,
            distribution=distribution,
            status="failed",
            error_codes=("registrar_contract_invalid",),
            exception_class=_project_exception_class(exc),
        )
    try:
        registered = loaded()
    except Exception as exc:  # noqa: BLE001 - diagnostic boundary
        return _InternalPluginDiagnostic(
            identity=identity,
            distribution=distribution,
            status="failed",
            error_codes=("registrar_call_failed",),
            exception_class=_project_exception_class(exc),
        )
    if not isinstance(registered, Mapping):
        return _InternalPluginDiagnostic(
            identity=identity,
            distribution=distribution,
            status="failed",
            error_codes=("registrar_returned_non_mapping",),
        )
    try:
        raw_names = list(islice(iter(registered), _DIAGNOSTIC_ACTION_LIMIT + 1))
    except Exception as exc:  # noqa: BLE001 - diagnostic boundary
        return _InternalPluginDiagnostic(
            identity=identity,
            distribution=distribution,
            status="failed",
            error_codes=("registrar_mapping_unreadable",),
            exception_class=_project_exception_class(exc),
        )
    action_identities = tuple(
        project_diagnostic_identity(raw_action, kind="action")
        for raw_action in raw_names[:_DIAGNOSTIC_ACTION_LIMIT]
    )
    if len(raw_names) > _DIAGNOSTIC_ACTION_LIMIT:
        return _InternalPluginDiagnostic(
            identity=identity,
            distribution=distribution,
            status="failed",
            error_codes=("registered_action_limit_exceeded",),
            registered_actions=action_identities,
        )
    try:
        raw_action_names = set(raw_names)
        raw_collisions = sorted(raw_action_names & registered_names)
    except Exception as exc:  # noqa: BLE001 - mirror strict comparison safely
        return _InternalPluginDiagnostic(
            identity=identity,
            distribution=distribution,
            status="failed",
            error_codes=("registrar_mapping_unreadable",),
            exception_class=_project_exception_class(exc),
        )
    if raw_collisions:
        collision_names = set(raw_collisions)
        conflicting_actions = tuple(
            action_identity
            for raw_action, action_identity in zip(raw_names, action_identities, strict=True)
            if raw_action in collision_names
        )
        return _InternalPluginDiagnostic(
            identity=identity,
            distribution=distribution,
            status="failed",
            error_codes=("action_collision",),
            registered_actions=tuple(
                sorted(action_identities, key=lambda action: action.display)
            ),
            conflicting_actions=tuple(
                sorted(conflicting_actions, key=lambda action: action.display)
            ),
        )
    registered_names.update(raw_action_names)
    return _InternalPluginDiagnostic(
        identity=identity,
        distribution=distribution,
        status="passed",
        registered_actions=tuple(sorted(action_identities, key=lambda action: action.display)),
    )


def _serialize_internal_action_plugin_inventory(
    snapshot: _InternalPluginSnapshot,
) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for diagnostic in snapshot.diagnostics:
        item: dict[str, Any] = {
            "name": diagnostic.identity.display,
            "entry_group": INTERNAL_ACTION_ENTRY_GROUP,
            "trusted_in_process": True,
            "contract": "rexecop.internal_action_registrar.v1",
        }
        if diagnostic.status == "failed":
            item.update(
                {
                    "distribution": diagnostic.distribution.display,
                    "status": "failed",
                    "errors": list(diagnostic.error_codes[:4]),
                    "registered_actions": [
                        action.display
                        for action in diagnostic.registered_actions[:_DIAGNOSTIC_ACTION_LIMIT]
                    ],
                }
            )
            if diagnostic.conflicting_actions:
                item["conflicting_actions"] = [
                    action.display
                    for action in diagnostic.conflicting_actions[:_DIAGNOSTIC_ACTION_LIMIT]
                ]
            if diagnostic.exception_class is not None:
                item["exception_class"] = diagnostic.exception_class.display
        inventory.append(item)
    return inventory


def _entry_point_distribution_identity(ep: Any) -> ProjectedDiagnosticIdentity:
    try:
        raw_distribution = ep.dist.name
    except Exception:  # noqa: BLE001 - missing/broken metadata is not incompatibility
        raw_distribution = "unknown"
    return project_diagnostic_identity(raw_distribution, kind="distribution")


def _project_exception_class(exc: Exception) -> ProjectedDiagnosticIdentity:
    try:
        raw_class_name = type(exc).__name__
    except Exception:  # noqa: BLE001 - untrusted exception class metadata
        raw_class_name = "Exception"
    return project_diagnostic_identity(raw_class_name, kind="exception")


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
