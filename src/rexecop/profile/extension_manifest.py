from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from rexecop import __version__
from rexecop.catalog.digest import canonical_digest
from rexecop.connectors.fixture_loader import (
    connector_backend_plugin_inventory,
)
from rexecop.connectors.registry import list_connector_backend_descriptors
from rexecop.execution.internal_registry import (
    _InternalPluginDiagnostic,
    _serialize_internal_action_plugin_inventory,
    _snapshot_internal_action_plugins,
    list_registered_internal_actions,
)
from rexecop.plugins.diagnostic_identity import (
    ProjectedDiagnosticIdentity,
    project_diagnostic_identity,
)
from rexecop.profile.resolver import list_registered_profiles

EXTENSION_MANIFEST_SCHEMA = "rexecop.extension_manifest.v0.1"
REQUIRED_CONTRACTS = (
    "profile_contract",
    "connector_contract",
    "observation_envelope.v0.1",
    "reaction_plan.v0.1",
)
SUPPORTED_TRACKS = ("readonly", "mutation", "all")
SECRET_RESOLVERS = (
    {
        "name": "env",
        "source": "rexecop.core",
        "mechanism": "REXECOP_SECRET_<REF>",
    },
    {
        "name": "file",
        "source": "rexecop.core",
        "mechanism": "REXECOP_SECRETS_FILE",
    },
)
_DIAGNOSTIC_INVENTORY_LIMIT = 64
_DIAGNOSTIC_ACTION_LIMIT = 64


@dataclass(frozen=True, slots=True)
class _PluginCompatibilitySnapshot:
    report: dict[str, Any]
    installed_entry_points: tuple[ProjectedDiagnosticIdentity, ...]


def build_extension_manifest() -> dict[str, Any]:
    connector_backends = [item.as_dict() for item in list_connector_backend_descriptors()]
    internal_actions = [
        {
            "name": name,
            "source": (
                "rexecop.core" if name in _CORE_INTERNAL_ACTIONS else "rexecop.internal_actions"
            ),
            "compatibility_version": __version__,
        }
        for name in list_registered_internal_actions()
    ]
    payload = {
        "schema": EXTENSION_MANIFEST_SCHEMA,
        "compatibility_version": __version__,
        "required_contracts": list(REQUIRED_CONTRACTS),
        "supported_tracks": list(SUPPORTED_TRACKS),
        "profiles": [
            {
                "name": name,
                "source": "rexecop.profiles",
                "compatibility_version": __version__,
            }
            for name in list_registered_profiles()
        ],
        "connector_backends": connector_backends,
        "internal_actions": internal_actions,
        "secret_resolvers": [dict(item) for item in SECRET_RESOLVERS],
        "plugin_entry_groups": {
            "profiles": "rexecop.profiles",
            "connector_backends": "rexecop.connector_backends",
            "internal_actions": "rexecop.internal_actions",
        },
        "plugin_security_posture": {
            "execution_model": "trusted_in_process",
            "sandboxed": False,
            "connector_factory_contract": "rexecop.connector_backend_factory.v1",
            "internal_registrar_contract": "rexecop.internal_action_registrar.v1",
            "allowlist_env": "REXECOP_PLUGIN_ALLOWLIST",
        },
    }
    payload["digest"] = canonical_digest(
        {key: value for key, value in payload.items() if key != "digest"}
    )
    return payload


_CORE_INTERNAL_ACTIONS = frozenset(
    {
        "record_execution_checkpoint",
        "record_rollback_marker",
    }
)


def build_plugin_compatibility_report() -> dict[str, Any]:
    return _build_plugin_compatibility_snapshot().report


def _build_plugin_compatibility_snapshot() -> _PluginCompatibilitySnapshot:
    incompatible_plugins: list[dict[str, Any]] = []
    incompatible_keys: set[tuple[str, str]] = set()
    connector_items: list[dict[str, Any]] = []
    connector_inventory: list[dict[str, Any]] = []
    installed_entry_points: list[ProjectedDiagnosticIdentity] = []
    try:
        raw_connector_inventory = connector_backend_plugin_inventory()
    except Exception as exc:  # noqa: BLE001 - compatibility report must be bounded
        raw_connector_inventory = []
        identity = project_diagnostic_identity("unknown", kind="entry")
        distribution = project_diagnostic_identity("unknown", kind="distribution")
        exception_class = _project_exception_class(exc)
        connector_inventory.append(
            _failed_connector_inventory_item(
                identity,
                error_codes=("entry_point_enumeration_failed",),
                exception_class=exception_class,
            )
        )
        connector_items.append(
            _connector_compatibility_item(
                identity,
                status="failed",
                error_codes=("entry_point_enumeration_failed",),
                exception_class=exception_class,
            )
        )
        _append_incompatible_plugin(
            incompatible_plugins,
            incompatible_keys,
            identity=identity,
            distribution=distribution,
            kind="connector_backend",
            entry_group="rexecop.connector_backends",
            reason_codes=("entry_point_enumeration_failed",),
            exception_class=exception_class,
        )

    inventory_overflow = len(raw_connector_inventory) > _DIAGNOSTIC_INVENTORY_LIMIT
    visible_limit = (
        _DIAGNOSTIC_INVENTORY_LIMIT - 1 if inventory_overflow else _DIAGNOSTIC_INVENTORY_LIMIT
    )
    for raw_inventory in raw_connector_inventory[:visible_limit]:
        raw_name = cast(str, raw_inventory.get("name"))
        identity = project_diagnostic_identity(raw_name, kind="entry")
        installed_entry_points.append(identity)
        projected_inventory = {
            "name": identity.display,
            "entry_group": raw_inventory["entry_group"],
            "trusted_in_process": raw_inventory["trusted_in_process"],
            "contract": raw_inventory["contract"],
            "name_collision": raw_inventory["name_collision"],
        }
        connector_inventory.append(projected_inventory)
        item = _connector_compatibility_item(identity)
        if projected_inventory["name_collision"]:
            item["status"] = "failed"
            item["errors"] = ["plugin_name_collision"]
            _append_incompatible_plugin(
                incompatible_plugins,
                incompatible_keys,
                identity=identity,
                distribution=project_diagnostic_identity("unknown", kind="distribution"),
                kind="connector_backend",
                entry_group="rexecop.connector_backends",
                reason_codes=("plugin_name_collision",),
            )
            connector_items.append(item)
            continue
        try:
            from rexecop.connectors.fixture_loader import load_connector_backend_for_connector

            runtime = load_connector_backend_for_connector(
                raw_name,
                connector_name="compatibility_probe",
                config={},
                profile_root=None,
                mutating_allowed=False,
            )
            if runtime is None or not hasattr(runtime, "invoke"):
                item["status"] = "failed"
                item["errors"] = ["factory_returned_invalid_runtime"]
                _append_incompatible_plugin(
                    incompatible_plugins,
                    incompatible_keys,
                    identity=identity,
                    distribution=project_diagnostic_identity(
                        "unknown", kind="distribution"
                    ),
                    kind="connector_backend",
                    entry_group="rexecop.connector_backends",
                    reason_codes=("factory_returned_invalid_runtime",),
                )
        except Exception as exc:  # noqa: BLE001 - compatibility report must be bounded
            exception_class = _project_exception_class(exc)
            item["status"] = "failed"
            item["errors"] = ["plugin_probe_failed"]
            item["exception_class"] = exception_class.display
            _append_incompatible_plugin(
                incompatible_plugins,
                incompatible_keys,
                identity=identity,
                distribution=project_diagnostic_identity("unknown", kind="distribution"),
                kind="connector_backend",
                entry_group="rexecop.connector_backends",
                reason_codes=("plugin_probe_failed",),
                exception_class=exception_class,
            )
        connector_items.append(item)

    if inventory_overflow:
        identity = project_diagnostic_identity("inventory_limit", kind="entry")
        error_codes = ("entry_point_inventory_limit_exceeded",)
        connector_inventory.append(
            _failed_connector_inventory_item(identity, error_codes=error_codes)
        )
        connector_items.append(
            _connector_compatibility_item(
                identity,
                status="failed",
                error_codes=error_codes,
            )
        )
        _append_incompatible_plugin(
            incompatible_plugins,
            incompatible_keys,
            identity=identity,
            distribution=project_diagnostic_identity("unknown", kind="distribution"),
            kind="connector_backend",
            entry_group="rexecop.connector_backends",
            reason_codes=error_codes,
        )

    internal_snapshot = _snapshot_internal_action_plugins()
    internal_inventory = _serialize_internal_action_plugin_inventory(internal_snapshot)
    installed_entry_points.extend(internal_snapshot.installed_entry_points)
    internal_items = [
        {
            "name": name,
            "kind": "internal_action",
            "entry_group": "rexecop.internal_actions",
            "status": "passed",
            "errors": [],
            "contract": "rexecop.internal_action_registrar.v1",
            "trusted_in_process": name not in _CORE_INTERNAL_ACTIONS,
        }
        for name in sorted(_CORE_INTERNAL_ACTIONS)
    ]
    for diagnostic in internal_snapshot.diagnostics:
        if diagnostic.status != "passed":
            _append_internal_incompatibility(
                incompatible_plugins,
                incompatible_keys,
                diagnostic,
            )
            continue
        for action_identity in diagnostic.registered_actions:
            internal_items.append(
                {
                    "name": action_identity.display,
                    "kind": "internal_action",
                    "entry_group": "rexecop.internal_actions",
                    "status": "passed",
                    "errors": [],
                    "contract": "rexecop.internal_action_registrar.v1",
                    "trusted_in_process": True,
                }
            )

    internal_items.sort(key=lambda item: str(item["name"]))

    if len(internal_items) > _DIAGNOSTIC_ACTION_LIMIT:
        internal_items = internal_items[:_DIAGNOSTIC_ACTION_LIMIT]
        _append_incompatible_plugin(
            incompatible_plugins,
            incompatible_keys,
            identity=project_diagnostic_identity(
                "internal_action_inventory_limit", kind="action"
            ),
            distribution=project_diagnostic_identity("unknown", kind="distribution"),
            kind="internal_action",
            entry_group="rexecop.internal_actions",
            reason_codes=("registered_action_inventory_limit_exceeded",),
        )
    if len(incompatible_plugins) > _DIAGNOSTIC_INVENTORY_LIMIT:
        incompatible_plugins = incompatible_plugins[: _DIAGNOSTIC_INVENTORY_LIMIT - 1]
        limit_identity = project_diagnostic_identity(
            "incompatibility_inventory_limit", kind="entry"
        )
        incompatible_plugins.append(
            _incompatible_plugin_payload(
                identity=limit_identity,
                distribution=project_diagnostic_identity("unknown", kind="distribution"),
                kind="plugin_inventory",
                entry_group="unknown",
                reason_codes=("incompatibility_inventory_limit_exceeded",),
            )
        )
    failures = [str(incompatible["name"]) for incompatible in incompatible_plugins]
    report = {
        "schema": "rexecop.plugin_compatibility_report.v0.1",
        "status": "passed" if not failures else "failed",
        "connector_backends": connector_items,
        "internal_actions": internal_items,
        "inventory": {
            "connector_backends": connector_inventory,
            "internal_action_registrars": internal_inventory,
        },
        "failed": failures,
        "incompatible_plugins": incompatible_plugins,
        "security_posture": {
            "execution_model": "trusted_in_process",
            "sandboxed": False,
            "allowlist_required_for_stable": True,
        },
    }
    return _PluginCompatibilitySnapshot(
        report=report,
        installed_entry_points=tuple(installed_entry_points),
    )


def _connector_compatibility_item(
    identity: ProjectedDiagnosticIdentity,
    *,
    status: str = "passed",
    error_codes: tuple[str, ...] = (),
    exception_class: ProjectedDiagnosticIdentity | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": identity.display,
        "kind": "connector_backend",
        "entry_group": "rexecop.connector_backends",
        "status": status,
        "errors": list(error_codes[:4]),
        "contract": "rexecop.connector_backend_factory.v1",
        "trusted_in_process": True,
    }
    if exception_class is not None:
        item["exception_class"] = exception_class.display
    return item


def _failed_connector_inventory_item(
    identity: ProjectedDiagnosticIdentity,
    *,
    error_codes: tuple[str, ...],
    exception_class: ProjectedDiagnosticIdentity | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": identity.display,
        "entry_group": "rexecop.connector_backends",
        "trusted_in_process": True,
        "contract": "rexecop.connector_backend_factory.v1",
        "name_collision": False,
        "status": "failed",
        "errors": list(error_codes[:4]),
    }
    if exception_class is not None:
        item["exception_class"] = exception_class.display
    return item


def _append_internal_incompatibility(
    incompatible_plugins: list[dict[str, Any]],
    incompatible_keys: set[tuple[str, str]],
    diagnostic: _InternalPluginDiagnostic,
) -> None:
    _append_incompatible_plugin(
        incompatible_plugins,
        incompatible_keys,
        identity=diagnostic.identity,
        distribution=diagnostic.distribution,
        kind="internal_action",
        entry_group="rexecop.internal_actions",
        reason_codes=diagnostic.error_codes,
        exception_class=diagnostic.exception_class,
        registered_actions=diagnostic.registered_actions,
        conflicting_actions=diagnostic.conflicting_actions,
    )


def _append_incompatible_plugin(
    incompatible_plugins: list[dict[str, Any]],
    incompatible_keys: set[tuple[str, str]],
    *,
    identity: ProjectedDiagnosticIdentity,
    distribution: ProjectedDiagnosticIdentity,
    kind: str,
    entry_group: str,
    reason_codes: tuple[str, ...],
    exception_class: ProjectedDiagnosticIdentity | None = None,
    registered_actions: tuple[ProjectedDiagnosticIdentity, ...] = (),
    conflicting_actions: tuple[ProjectedDiagnosticIdentity, ...] = (),
) -> None:
    correlation_key = (kind, identity.full_digest)
    if correlation_key in incompatible_keys:
        return
    incompatible_keys.add(correlation_key)
    incompatible_plugins.append(
        _incompatible_plugin_payload(
            identity=identity,
            distribution=distribution,
            kind=kind,
            entry_group=entry_group,
            reason_codes=reason_codes,
            exception_class=exception_class,
            registered_actions=registered_actions,
            conflicting_actions=conflicting_actions,
        )
    )


def _incompatible_plugin_payload(
    *,
    identity: ProjectedDiagnosticIdentity,
    distribution: ProjectedDiagnosticIdentity,
    kind: str,
    entry_group: str,
    reason_codes: tuple[str, ...],
    exception_class: ProjectedDiagnosticIdentity | None = None,
    registered_actions: tuple[ProjectedDiagnosticIdentity, ...] = (),
    conflicting_actions: tuple[ProjectedDiagnosticIdentity, ...] = (),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": identity.display,
        "distribution": distribution.display,
        "kind": kind,
        "entry_group": entry_group,
        "reason_codes": list(reason_codes[:4]),
    }
    if exception_class is not None:
        payload["exception_class"] = exception_class.display
    if registered_actions:
        payload["registered_actions"] = [
            action.display for action in registered_actions[:_DIAGNOSTIC_ACTION_LIMIT]
        ]
    if conflicting_actions:
        payload["conflicting_actions"] = [
            action.display for action in conflicting_actions[:_DIAGNOSTIC_ACTION_LIMIT]
        ]
    return payload


def _project_exception_class(exc: Exception) -> ProjectedDiagnosticIdentity:
    try:
        raw_class_name = type(exc).__name__
    except Exception:  # noqa: BLE001 - untrusted exception class metadata
        raw_class_name = "Exception"
    return project_diagnostic_identity(raw_class_name, kind="exception")
