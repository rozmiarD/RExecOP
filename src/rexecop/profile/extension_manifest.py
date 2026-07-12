from __future__ import annotations

from typing import Any

from rexecop import __version__
from rexecop.catalog.digest import canonical_digest
from rexecop.connectors.fixture_loader import (
    connector_backend_plugin_inventory,
)
from rexecop.connectors.registry import list_connector_backend_descriptors
from rexecop.execution.internal_registry import (
    internal_action_plugin_inventory,
    list_registered_internal_actions,
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
    connector_items: list[dict[str, Any]] = []
    for inventory in connector_backend_plugin_inventory():
        name = str(inventory["name"])
        item = {
            "name": name,
            "kind": "connector_backend",
            "entry_group": "rexecop.connector_backends",
            "status": "passed",
            "errors": [],
            "contract": inventory["contract"],
            "trusted_in_process": True,
        }
        if inventory["name_collision"]:
            item["status"] = "failed"
            item["errors"] = ["plugin_name_collision"]
            connector_items.append(item)
            continue
        try:
            from rexecop.connectors.fixture_loader import load_connector_backend_for_connector

            runtime = load_connector_backend_for_connector(
                name,
                connector_name="compatibility_probe",
                config={},
                profile_root=None,
                mutating_allowed=False,
            )
            if runtime is None or not hasattr(runtime, "invoke"):
                item["status"] = "failed"
                item["errors"] = ["factory_returned_invalid_runtime"]
        except Exception as exc:  # noqa: BLE001 - compatibility report must be bounded
            item["status"] = "failed"
            item["errors"] = [f"plugin_probe_failed:{type(exc).__name__}"]
        connector_items.append(item)

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
        for name in list_registered_internal_actions()
    ]

    failures = [
        item["name"] for item in connector_items + internal_items if item["status"] != "passed"
    ]
    return {
        "schema": "rexecop.plugin_compatibility_report.v0.1",
        "status": "passed" if not failures else "failed",
        "connector_backends": connector_items,
        "internal_actions": internal_items,
        "inventory": {
            "connector_backends": connector_backend_plugin_inventory(),
            "internal_action_registrars": internal_action_plugin_inventory(),
        },
        "failed": failures,
        "security_posture": {
            "execution_model": "trusted_in_process",
            "sandboxed": False,
            "allowlist_required_for_stable": True,
        },
    }
