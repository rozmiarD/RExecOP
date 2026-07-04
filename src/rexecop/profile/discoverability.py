from __future__ import annotations

from pathlib import Path
from typing import Any

from rexecop.catalog.service import compile_operation_descriptor
from rexecop.connectors.registry import (
    CONNECTOR_BACKEND_DESCRIPTOR_SCHEMA,
    describe_connector_backend,
    list_connector_backend_descriptors,
)
from rexecop.errors import RExecOpValidationError
from rexecop.execution.internal_registry import list_registered_internal_actions
from rexecop.profile.conformance import validate_profile_conformance
from rexecop.profile.extension_manifest import (
    EXTENSION_MANIFEST_SCHEMA,
    SECRET_RESOLVERS,
    build_extension_manifest,
    build_plugin_compatibility_report,
)
from rexecop.profile.loader import LoadedProfile, load_profile
from rexecop.profile.resolver import list_registered_profiles, resolve_profile_path

PROFILE_LIST_SCHEMA = "rexecop.profile_list.v0.1"
PROFILE_SHOW_SCHEMA = "rexecop.profile_show.v0.1"
CONNECTOR_LIST_SCHEMA = "rexecop.connector_list.v0.1"
CONNECTOR_SHOW_SCHEMA = "rexecop.connector_show.v0.1"
CAPABILITY_LIST_SCHEMA = "rexecop.capability_list.v0.1"
PROFILE_DEVELOPER_CHECK_SCHEMA = "rexecop.profile_developer_check.v0.1"


def list_profiles_manifest() -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    for name in list_registered_profiles():
        try:
            summary = _profile_summary(name, include_intents=False)
        except RExecOpValidationError as exc:
            summary = {
                "name": name,
                "source": "rexecop.profiles",
                "status": "failed",
                "errors": [str(exc)],
            }
        profiles.append(summary)
    return {
        "schema": PROFILE_LIST_SCHEMA,
        "profiles": profiles,
    }


def show_profile_manifest(profile: str | Path, *, track: str = "readonly") -> dict[str, Any]:
    loaded = load_profile(resolve_profile_path(profile))
    summary = _profile_summary(profile, include_intents=True)
    readonly = validate_profile_conformance(profile, track="readonly")
    mutation = validate_profile_conformance(profile, track="mutation")
    developer_check = run_profile_developer_check(profile, track=track)
    manifest = build_extension_manifest()
    return {
        "schema": PROFILE_SHOW_SCHEMA,
        "profile": summary,
        "tracks": {
            "readonly": readonly.as_dict(),
            "mutation": mutation.as_dict(),
        },
        "compatibility": {
            "readonly": readonly.status,
            "mutation": mutation.status,
            "plugins": developer_check["plugin_compatibility"]["status"],
        },
        "developer_check": developer_check,
        "extension_manifest": {
            "schema": EXTENSION_MANIFEST_SCHEMA,
            "profile": loaded.name,
            "profile_version": loaded.version,
            "required_contracts": manifest["required_contracts"],
            "supported_tracks": manifest["supported_tracks"],
        },
    }


def run_profile_developer_check(
    profile: str | Path,
    *,
    track: str = "readonly",
) -> dict[str, Any]:
    """Run conformance and plugin compatibility without a runtime store."""
    conformance = validate_profile_conformance(profile, track=track)
    plugins = build_plugin_compatibility_report()
    status = "passed"
    if conformance.status != "passed" or plugins["status"] != "passed":
        status = "failed"
    return {
        "schema": PROFILE_DEVELOPER_CHECK_SCHEMA,
        "status": status,
        "profile": conformance.profile,
        "track": conformance.track,
        "conformance": conformance.as_dict(),
        "plugin_compatibility": plugins,
        "requires_runtime_store": False,
    }


def list_connectors_manifest() -> dict[str, Any]:
    return {
        "schema": CONNECTOR_LIST_SCHEMA,
        "connector_backends": [item.as_dict() for item in list_connector_backend_descriptors()],
    }


def show_connector_manifest(backend: str) -> dict[str, Any]:
    try:
        descriptor = describe_connector_backend(backend)
    except KeyError as exc:
        raise RExecOpValidationError(f"connector backend not found: {backend}") from exc
    compatibility = build_plugin_compatibility_report()
    plugin_status = "not_applicable"
    for item in compatibility["connector_backends"]:
        if item["name"] == backend:
            plugin_status = item["status"]
            break
    return {
        "schema": CONNECTOR_SHOW_SCHEMA,
        "descriptor_schema": CONNECTOR_BACKEND_DESCRIPTOR_SCHEMA,
        "connector_backend": descriptor.as_dict(),
        "plugin_compatibility": plugin_status,
    }


def list_capabilities_manifest() -> dict[str, Any]:
    capabilities: dict[str, dict[str, str]] = {}
    for descriptor in list_connector_backend_descriptors():
        for capability in descriptor.capability_descriptors:
            capabilities.setdefault(
                capability,
                {
                    "capability": capability,
                    "source": descriptor.source,
                    "backend_class": descriptor.backend_class,
                },
            )
    for action in list_registered_internal_actions():
        capability = f"internal.{action}"
        capabilities[capability] = {
            "capability": capability,
            "source": "rexecop.core"
            if action in {"record_execution_checkpoint", "record_rollback_marker"}
            else "rexecop.internal_actions",
            "backend_class": action,
        }
    for resolver in SECRET_RESOLVERS:
        capability = f"secret.{resolver['name']}"
        capabilities[capability] = {
            "capability": capability,
            "source": resolver["source"],
            "backend_class": resolver["name"],
        }
    return {
        "schema": CAPABILITY_LIST_SCHEMA,
        "capabilities": [capabilities[key] for key in sorted(capabilities)],
    }


def _profile_summary(profile: str | Path, *, include_intents: bool) -> dict[str, Any]:
    loaded = load_profile(resolve_profile_path(profile))
    operations, catalog_errors = _compile_profile_operations(loaded)
    required_capabilities = sorted(
        {
            capability
            for operation in operations
            for capability in operation.required_capabilities
        }
    )
    readonly = validate_profile_conformance(profile, track="readonly")
    mutation = validate_profile_conformance(profile, track="mutation")
    payload: dict[str, Any] = {
        "name": loaded.name,
        "version": loaded.version,
        "source": _profile_source(profile),
        "required_capabilities": required_capabilities,
        "intent_count": len(operations),
        "catalog_errors": catalog_errors,
        "compatibility": {
            "readonly": readonly.status,
            "mutation": mutation.status,
        },
    }
    if include_intents:
        payload["intents"] = [
            {
                "id": operation.id,
                "modes": list(operation.modes),
                "required_capabilities": list(operation.required_capabilities),
                "required_connectors": list(operation.required_connectors),
                "side_effect_class": operation.side_effect_class,
            }
            for operation in operations
        ]
        payload["tracks"] = {
            "readonly": {
                "checked_intents": list(readonly.checked_intents),
                "skipped_intents": list(readonly.skipped_intents),
            },
            "mutation": {
                "checked_intents": list(mutation.checked_intents),
                "mutation_candidate_intents": list(mutation.mutation_candidate_intents),
            },
        }
    return payload


def _compile_profile_operations(
    loaded: LoadedProfile,
) -> tuple[list[Any], list[str]]:
    operations: list[Any] = []
    errors: list[str] = []
    intents_dir = loaded.root / "intents"
    if not intents_dir.is_dir():
        return operations, ["intents:missing"]
    for path in sorted(intents_dir.glob("*.yaml")):
        try:
            operations.append(compile_operation_descriptor(loaded, path.stem))
        except RExecOpValidationError as exc:
            errors.append(f"{path.stem}:{exc}")
    return operations, errors


def _profile_source(profile: str | Path) -> str:
    text = str(profile).strip()
    if isinstance(profile, Path) or "/" in text or "\\" in text or text.startswith("."):
        return "path"
    if text in list_registered_profiles():
        return "rexecop.profiles"
    return "path"