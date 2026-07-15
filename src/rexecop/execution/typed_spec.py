from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rexecop.catalog.digest import canonical_digest
from rexecop.connectors.action_shape import (
    canonical_http_action_shape,
    http_action_shape_digest,
    validate_http_action_shape,
)
from rexecop.connectors.capability_descriptor import compile_connector_capability_descriptor
from rexecop.connectors.command_shape import normalize_allowlisted_argv
from rexecop.connectors.errors import READ_ONLY_MODES
from rexecop.connectors.fixture_loader import list_registered_connector_backends
from rexecop.connectors.http_support import destination_binding
from rexecop.errors import RExecOpValidationError
from rexecop.profile.loader import LoadedProfile, load_profile
from rexecop.profile.resolver import resolve_profile_path

STEP_EXECUTION_SPEC_SCHEMA = "rexecop.step_execution_spec.v0.1"
COMMAND_EXECUTION_SPEC_SCHEMA = "rexecop.command_execution_spec.v0.1"
HTTP_ACTION_EXECUTION_SPEC_SCHEMA = "rexecop.http_action_execution_spec.v0.1"
STATIC_FIXTURE_EXECUTION_SPEC_SCHEMA = "rexecop.static_fixture_execution_spec.v0.1"
PROFILE_CONNECTOR_EXECUTION_SPEC_SCHEMA = "rexecop.profile_connector_execution_spec.v0.1"

TYPED_EXECUTION_SCHEMA_VERSION = "v0.1"
SUPPORTED_TYPED_EXECUTION_SCHEMAS: dict[str, frozenset[str]] = {
    STEP_EXECUTION_SPEC_SCHEMA: frozenset({TYPED_EXECUTION_SCHEMA_VERSION}),
    COMMAND_EXECUTION_SPEC_SCHEMA: frozenset({TYPED_EXECUTION_SCHEMA_VERSION}),
    HTTP_ACTION_EXECUTION_SPEC_SCHEMA: frozenset({TYPED_EXECUTION_SCHEMA_VERSION}),
    STATIC_FIXTURE_EXECUTION_SPEC_SCHEMA: frozenset({TYPED_EXECUTION_SCHEMA_VERSION}),
    PROFILE_CONNECTOR_EXECUTION_SPEC_SCHEMA: frozenset({TYPED_EXECUTION_SCHEMA_VERSION}),
}

_RUNTIME_PROJECTION_NON_CLAIMS = (
    "Runtime projection only; not a SCLite truth artifact.",
    "Does not prove GovEngine admission or host enforcement occurred.",
    "Does not embed resolved secret values or connector endpoint configuration.",
)


def compile_step_execution_spec(
    *,
    step: Mapping[str, Any],
    profile: LoadedProfile | str | Path,
    connector_config: Mapping[str, Any],
    mode: str,
) -> dict[str, Any]:
    """Compile one digest-bound typed execution projection for a connector step."""
    if isinstance(profile, LoadedProfile):
        loaded = profile
    else:
        loaded = load_profile(resolve_profile_path(profile))
    step_id = str(step.get("id") or step.get("step_id") or "").strip()
    if not step_id:
        raise RExecOpValidationError("typed execution step missing id")
    if str(step.get("type") or "") != "connector":
        raise RExecOpValidationError("typed execution spec requires connector step")
    connector = str(step.get("connector") or "").strip()
    action = str(step.get("action") or "").strip()
    if not connector or not action:
        raise RExecOpValidationError("typed execution step missing connector or action")

    contract = loaded.connector_contract(connector) or {}
    backend = str(
        connector_config.get("backend")
        or connector_config.get("mode")
        or contract.get("backend")
        or ""
    ).strip()
    capability_descriptor = compile_connector_capability_descriptor(
        connector=connector,
        backend_class=backend,
        connector_config=connector_config,
        mode=mode,
    )
    if backend == "http_api":
        payload = _compile_http_action_execution_spec(
            connector=connector,
            action=action,
            contract=contract,
            connector_config=connector_config,
        )
        payload_schema = HTTP_ACTION_EXECUTION_SPEC_SCHEMA
    elif backend in {"local_shell_readonly", "ssh_readonly"}:
        payload = _compile_command_execution_spec(
            connector=connector,
            action=action,
            backend=backend,
            contract=contract,
            connector_config=connector_config,
            mode=mode,
        )
        payload_schema = COMMAND_EXECUTION_SPEC_SCHEMA
    elif backend == "static_fixture":
        payload = _compile_static_fixture_execution_spec(
            connector=connector,
            action=action,
            connector_config=connector_config,
            mode=mode,
        )
        payload_schema = STATIC_FIXTURE_EXECUTION_SPEC_SCHEMA
    elif backend in list_registered_connector_backends():
        payload = _compile_profile_connector_execution_spec(
            connector=connector,
            action=action,
            backend=backend,
            connector_config=connector_config,
            mode=mode,
        )
        payload_schema = PROFILE_CONNECTOR_EXECUTION_SPEC_SCHEMA
    else:
        raise RExecOpValidationError(f"typed execution unsupported backend: {backend or 'missing'}")

    spec = {
        "schema": STEP_EXECUTION_SPEC_SCHEMA,
        "schema_version": TYPED_EXECUTION_SCHEMA_VERSION,
        "projection_kind": "runtime_projection",
        "step_id": step_id,
        "connector": connector,
        "action": action,
        "backend_class": backend,
        "read_only": mode in READ_ONLY_MODES,
        "payload_schema": payload_schema,
        "payload": payload,
        "capability_descriptor": capability_descriptor,
        "required_capability_descriptors": _required_capability_descriptors(
            contract,
            backend=backend,
            action=action,
        ),
        "network_policy_binding": _network_policy_binding(contract, backend=backend),
        "non_claims": list(_RUNTIME_PROJECTION_NON_CLAIMS),
    }
    spec["digest"] = step_execution_spec_digest(spec)
    validate_typed_execution_schema_version(spec)
    return spec


def _required_capability_descriptors(
    connector_contract: Mapping[str, Any],
    *,
    backend: str,
    action: str,
) -> list[str]:
    values: Any = None
    by_backend = connector_contract.get("required_capability_descriptors_by_backend")
    if isinstance(by_backend, Mapping):
        backend_requirements = by_backend.get(backend)
        if isinstance(backend_requirements, Mapping):
            values = backend_requirements.get(action)
        elif backend_requirements is not None:
            values = backend_requirements
    if values is None:
        values = connector_contract.get("required_capability_descriptors")
    if values is None:
        return []
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise RExecOpValidationError(
            "connector required_capability_descriptors must be a list of strings"
        )
    normalized = [value.strip() for value in values]
    if len(normalized) != len(set(normalized)):
        raise RExecOpValidationError(
            "connector required_capability_descriptors contains duplicates"
        )
    return normalized


def _network_policy_binding(
    connector_contract: Mapping[str, Any],
    *,
    backend: str,
) -> dict[str, Any]:
    raw: Any = None
    by_backend = connector_contract.get("network_policy_binding_by_backend")
    if isinstance(by_backend, Mapping):
        raw = by_backend.get(backend)
    if raw is None:
        raw = connector_contract.get("network_policy_binding")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise RExecOpValidationError("connector network_policy_binding must be a mapping")
    supported = {
        "allowed_network_egress",
        "allowed_network_schemes",
        "allowed_address_classes",
        "required_origin_binding_digest",
    }
    unknown = sorted(str(key) for key in raw if key not in supported)
    if unknown:
        raise RExecOpValidationError(
            "connector network_policy_binding contains unsupported fields: " + ", ".join(unknown)
        )
    normalized: dict[str, Any] = {}
    for key in (
        "allowed_network_egress",
        "allowed_network_schemes",
        "allowed_address_classes",
    ):
        values = raw.get(key)
        if values is None:
            continue
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise RExecOpValidationError(
                f"connector network_policy_binding {key} must be a list of strings"
            )
        items = [value.strip() for value in values]
        if len(items) != len(set(items)):
            raise RExecOpValidationError(
                f"connector network_policy_binding {key} contains duplicates"
            )
        normalized[key] = items
    if not normalized.get("allowed_network_egress"):
        raise RExecOpValidationError(
            "connector network_policy_binding allowed_network_egress is required"
        )
    origin_digest = raw.get("required_origin_binding_digest")
    if origin_digest is not None:
        if not isinstance(origin_digest, str) or not origin_digest.strip():
            raise RExecOpValidationError(
                "connector network_policy_binding required_origin_binding_digest "
                "must be a non-empty string"
            )
        normalized["required_origin_binding_digest"] = origin_digest.strip()
    return normalized


def step_execution_spec_digest(spec: Mapping[str, Any]) -> str:
    payload = {
        key: value for key, value in dict(spec).items() if key not in {"digest", "non_claims"}
    }
    return "sha256:" + canonical_digest(payload)


def validate_typed_execution_schema_version(spec: Mapping[str, Any]) -> None:
    schema = str(spec.get("schema") or "").strip()
    version = str(spec.get("schema_version") or "").strip()
    supported = SUPPORTED_TYPED_EXECUTION_SCHEMAS.get(schema)
    if not supported:
        raise RExecOpValidationError(f"unsupported typed execution schema: {schema or 'missing'}")
    if version not in supported:
        major = version.split(".", 1)[0] if version else ""
        if major and major not in {item.split(".", 1)[0] for item in supported}:
            raise RExecOpValidationError(
                f"unsupported typed execution schema major version: {version}"
            )
        raise RExecOpValidationError(f"unsupported typed execution schema version: {version}")


def assert_step_execution_spec_unchanged(
    *,
    step_id: str,
    spec: Mapping[str, Any],
    shared_state: Mapping[str, Any],
) -> None:
    specs = shared_state.get("typed_execution_specs")
    if not isinstance(specs, Mapping):
        return
    prior = specs.get(step_id)
    if not isinstance(prior, Mapping):
        return
    expected = str(prior.get("digest") or "").strip()
    actual = str(spec.get("digest") or "").strip()
    if expected and actual and expected != actual:
        raise RExecOpValidationError(f"typed execution spec drift detected for step {step_id}")


def bind_step_execution_spec(
    *,
    step_id: str,
    spec: Mapping[str, Any],
    shared_state: dict[str, Any],
) -> None:
    validate_typed_execution_schema_version(spec)
    assert_step_execution_spec_unchanged(
        step_id=step_id,
        spec=spec,
        shared_state=shared_state,
    )
    specs = shared_state.setdefault("typed_execution_specs", {})
    if step_id not in specs:
        capability = spec.get("capability_descriptor")
        capability_digest = ""
        if isinstance(capability, Mapping):
            capability_digest = str(capability.get("digest") or "")
        payload = spec.get("payload")
        destination = payload.get("destination_binding") if isinstance(payload, Mapping) else None
        specs[step_id] = {
            "schema": str(spec.get("schema") or ""),
            "digest": str(spec.get("digest") or ""),
            "payload_schema": str(spec.get("payload_schema") or ""),
            "capability_descriptor_digest": capability_digest,
        }
        if isinstance(destination, Mapping):
            specs[step_id]["destination_binding"] = dict(destination)


def _compile_http_action_execution_spec(
    *,
    connector: str,
    action: str,
    contract: Mapping[str, Any],
    connector_config: Mapping[str, Any],
) -> dict[str, Any]:
    shape_digest = validate_http_action_shape(
        connector_name=connector,
        action=action,
        connector_contract=dict(contract),
        connector_config=dict(connector_config),
    )
    actions = connector_config.get("actions")
    action_spec = actions.get(action) if isinstance(actions, Mapping) else None
    if not isinstance(action_spec, Mapping):
        raise RExecOpValidationError(f"http action not configured: {connector}.{action}")
    shape = canonical_http_action_shape(dict(action_spec), dict(connector_config))
    payload = {
        "schema": HTTP_ACTION_EXECUTION_SPEC_SCHEMA,
        "schema_version": TYPED_EXECUTION_SCHEMA_VERSION,
        "connector": connector,
        "action": action,
        "shape": shape,
        "shape_digest": shape_digest or http_action_shape_digest(shape),
        "mutating": bool(shape.get("mutating")),
        "max_response_bytes": int(shape.get("max_response_bytes") or 65536),
    }
    base_url = str(connector_config.get("base_url") or "").strip()
    declared_binding = connector_config.get("destination_binding")
    if base_url:
        payload["destination_binding"] = destination_binding(base_url)
    elif isinstance(declared_binding, Mapping):
        payload["destination_binding"] = dict(declared_binding)
    validate_typed_execution_schema_version(payload)
    return payload


def _compile_command_execution_spec(
    *,
    connector: str,
    action: str,
    backend: str,
    contract: Mapping[str, Any],
    connector_config: Mapping[str, Any],
    mode: str,
) -> dict[str, Any]:
    if mode not in READ_ONLY_MODES and backend.endswith("_readonly"):
        raise RExecOpValidationError(f"readonly backend {backend} refuses mutating mode {mode}")
    allowlist = connector_config.get("allowlist")
    if not isinstance(allowlist, list):
        raise RExecOpValidationError(f"command allowlist missing for connector {connector}")
    entry = _find_allowlist_entry(allowlist, action)
    if entry is None:
        raise RExecOpValidationError(f"command not allowlisted: {connector}.{action}")
    allowed_tools = {
        str(item.get("command")).strip().lower()
        for item in allowlist
        if isinstance(item, Mapping) and str(item.get("command") or "").strip()
    }
    tool = str(entry.get("command") or "").strip()
    args = entry.get("args") or []
    if not isinstance(args, list):
        raise RExecOpValidationError("allowlist args must be a list")
    argv = normalize_allowlisted_argv(
        tool=tool,
        args=args,
        allowed_tools=allowed_tools,
    )
    payload = {
        "schema": COMMAND_EXECUTION_SPEC_SCHEMA,
        "schema_version": TYPED_EXECUTION_SCHEMA_VERSION,
        "backend": backend,
        "connector": connector,
        "action": action,
        "argv": argv,
        "argv_digest": "sha256:" + canonical_digest({"argv": argv}),
        "max_output_bytes": int(connector_config.get("max_output_bytes") or 65536),
        "timeout_seconds": float(connector_config.get("timeout_seconds") or 10),
        "read_only": True,
    }
    validate_typed_execution_schema_version(payload)
    return payload


def _compile_profile_connector_execution_spec(
    *,
    connector: str,
    action: str,
    backend: str,
    connector_config: Mapping[str, Any],
    mode: str,
) -> dict[str, Any]:
    mutating = mode not in READ_ONLY_MODES
    payload = {
        "schema": PROFILE_CONNECTOR_EXECUTION_SPEC_SCHEMA,
        "schema_version": TYPED_EXECUTION_SCHEMA_VERSION,
        "connector": connector,
        "action": action,
        "backend_class": backend,
        "mutating": mutating,
        "action_digest": "sha256:"
        + canonical_digest(
            {
                "connector": connector,
                "action": action,
                "backend_class": backend,
                "mutating": mutating,
            }
        ),
    }
    validate_typed_execution_schema_version(payload)
    return payload


def _compile_static_fixture_execution_spec(
    *,
    connector: str,
    action: str,
    connector_config: Mapping[str, Any],
    mode: str,
) -> dict[str, Any]:
    actions = connector_config.get("actions")
    action_spec = actions.get(action) if isinstance(actions, Mapping) else None
    if not isinstance(action_spec, Mapping):
        raise RExecOpValidationError(f"static fixture action missing: {connector}.{action}")
    mutating = bool(action_spec.get("mutating"))
    if mutating and mode in READ_ONLY_MODES:
        raise RExecOpValidationError(
            f"static fixture mutating action refused in read-only mode: {action}"
        )
    payload = {
        "schema": STATIC_FIXTURE_EXECUTION_SPEC_SCHEMA,
        "schema_version": TYPED_EXECUTION_SCHEMA_VERSION,
        "connector": connector,
        "action": action,
        "fixture_only": bool(connector_config.get("fixture_only", True)),
        "mutating": mutating,
        "action_digest": "sha256:"
        + canonical_digest(
            {
                "connector": connector,
                "action": action,
                "mutating": mutating,
            }
        ),
    }
    validate_typed_execution_schema_version(payload)
    return payload


def _find_allowlist_entry(
    allowlist: list[Any],
    action: str,
) -> dict[str, Any] | None:
    for item in allowlist:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("action") or item.get("command") or "") == action:
            return dict(item)
    return None
