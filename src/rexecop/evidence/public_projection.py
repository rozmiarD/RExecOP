from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from rexecop.evidence.redaction import redact_payload
from rexecop.profile.loader import LoadedProfile, load_profile

PUBLIC_PROJECTION_SCHEMA = "rexecop.public_projection.v0.1"

TOP_LEVEL_SAFE_FIELDS = frozenset(
    {
        "step_id",
        "success",
        "error",
        "error_class",
        "status_code",
        "action_contract_digest",
        "output_digests",
        "output_truncated",
        "output_sizes",
        "max_output_bytes",
    }
)

OUTPUT_SAFE_FIELDS = frozenset(
    {
        "error_class",
        "status_code",
        "action_contract_digest",
        "output_digests",
        "output_truncated",
        "output_sizes",
        "max_output_bytes",
        "body_snippet",
        "before_state",
        "after_state",
    }
)

STRUCTURED_OUTPUT_SUBTREES = frozenset(
    {
        "before_state",
        "after_state",
        "output_digests",
        "output_truncated",
        "output_sizes",
    }
)


def resolve_public_projection_allowlist(
    *,
    profile: LoadedProfile | Path | str | None,
    connector: str,
    action: str,
) -> frozenset[str]:
    """Resolve profile-declared safe fields for one connector action."""
    loaded = _load_profile(profile)
    if loaded is None:
        return frozenset()
    contract = loaded.connector_contract(connector)
    if not isinstance(contract, Mapping):
        return frozenset()
    for shape_key in ("command_shapes", "action_shapes"):
        shapes = contract.get(shape_key)
        if not isinstance(shapes, Mapping):
            continue
        shape = shapes.get(action)
        if not isinstance(shape, Mapping):
            continue
        declared = _safe_fields_from_shape(shape)
        if declared:
            return declared
    return frozenset()


def sanitize_for_public_surface(
    payload: Any,
    *,
    allowlist: Collection[str] | None = None,
) -> Any:
    """Apply allowlist projection first, then finite redaction detectors."""
    projected = project_public_payload(payload, allowlist=allowlist)
    return redact_payload(projected)


def project_public_payload(
    payload: Any,
    *,
    allowlist: Collection[str] | None = None,
) -> Any:
    """Project raw-ish payloads through declared or default safe-field allowlists."""
    if not isinstance(payload, Mapping):
        return payload
    declared = frozenset(str(item).strip() for item in (allowlist or ()) if str(item).strip())
    return _project_mapping(payload, declared, path="")


def _load_profile(profile: LoadedProfile | Path | str | None) -> LoadedProfile | None:
    if profile is None:
        return None
    if isinstance(profile, LoadedProfile):
        return profile
    path = Path(profile)
    if not path.is_dir():
        return None
    return load_profile(path)


def _safe_fields_from_shape(shape: Mapping[str, Any]) -> frozenset[str]:
    projection = shape.get("public_projection")
    if isinstance(projection, Mapping):
        raw_fields = projection.get("safe_fields")
        if isinstance(raw_fields, list):
            return frozenset(
                str(item).strip()
                for item in raw_fields
                if str(item).strip()
            )
    legacy = shape.get("safe_output_fields")
    if isinstance(legacy, list):
        return frozenset(str(item).strip() for item in legacy if str(item).strip())
    return frozenset()


def _project_mapping(
    payload: Mapping[str, Any],
    allowlist: frozenset[str],
    *,
    path: str,
) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key, value in payload.items():
        child_path = f"{path}.{key}" if path else str(key)
        if str(key) == "output" and isinstance(value, Mapping):
            projected[key] = _project_output_mapping(value, allowlist)
            continue
        if _field_allowed(child_path, str(key), allowlist, nested_parent=path == ""):
            projected[key] = _project_value(value, allowlist, path=child_path)
        else:
            projected[key] = _digest_projection(value)
    return projected


def _project_output_mapping(
    payload: Mapping[str, Any],
    allowlist: frozenset[str],
) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key, value in payload.items():
        child_path = f"output.{key}"
        if _field_allowed(child_path, str(key), allowlist, nested_parent=False):
            if str(key) in STRUCTURED_OUTPUT_SUBTREES:
                projected[key] = value
            else:
                projected[key] = _project_value(value, allowlist, path=child_path)
        else:
            projected[key] = _digest_projection(value)
    return projected


def _project_value(
    value: Any,
    allowlist: frozenset[str],
    *,
    path: str,
) -> Any:
    if isinstance(value, Mapping):
        return _project_mapping(value, allowlist, path=path)
    if isinstance(value, list):
        return [_project_value(item, allowlist, path=path) for item in value]
    if isinstance(value, tuple):
        return tuple(_project_value(item, allowlist, path=path) for item in value)
    return value


def _field_allowed(
    path: str,
    key: str,
    allowlist: frozenset[str],
    *,
    nested_parent: bool,
) -> bool:
    if path in allowlist or key in allowlist:
        return True
    if nested_parent and key in TOP_LEVEL_SAFE_FIELDS:
        return True
    if path.startswith("output.") and key in OUTPUT_SAFE_FIELDS:
        return True
    return any(
        allowed.endswith(".*") and path.startswith(allowed[:-1])
        for allowed in allowlist
    )


def _digest_projection(value: Any) -> dict[str, str]:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "schema": PUBLIC_PROJECTION_SCHEMA,
        "projection": "digest_only",
        "digest": f"sha256:{digest}",
    }
