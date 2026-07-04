from __future__ import annotations

from typing import Any

from rexecop import __version__
from rexecop.catalog.digest import canonical_digest
from rexecop.connectors.fixture_loader import list_registered_connector_backends
from rexecop.connectors.registry import list_connector_backend_descriptors
from rexecop.execution.internal_registry import list_registered_internal_actions
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
                "rexecop.core"
                if name in _CORE_INTERNAL_ACTIONS
                else "rexecop.internal_actions"
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
    for name in list_registered_connector_backends():
        item = {
            "name": name,
            "kind": "connector_backend",
            "entry_group": "rexecop.connector_backends",
            "status": "passed",
            "errors": [],
        }
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
            item["errors"] = [f"{type(exc).__name__}:{exc}"]
        connector_items.append(item)

    internal_items = [
        {
            "name": name,
            "kind": "internal_action",
            "entry_group": "rexecop.internal_actions",
            "status": "passed",
            "errors": [],
        }
        for name in list_registered_internal_actions()
    ]

    failures = [
        item["name"]
        for item in connector_items + internal_items
        if item["status"] != "passed"
    ]
    return {
        "schema": "rexecop.plugin_compatibility_report.v0.1",
        "status": "passed" if not failures else "failed",
        "connector_backends": connector_items,
        "internal_actions": internal_items,
        "failed": failures,
    }