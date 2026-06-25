from __future__ import annotations

import hashlib
import json
from typing import Any

from rexecop.errors import RExecOpValidationError


def canonical_http_action_shape(
    action_spec: dict[str, Any], connector_config: dict[str, Any]
) -> dict[str, Any]:
    pagination = action_spec.get("pagination")
    pagination_shape = {}
    if isinstance(pagination, dict):
        pagination_shape = {
            "items_path": str(pagination.get("items_path") or ""),
            "next_path": str(pagination.get("next_path") or ""),
            "max_pages": int(pagination.get("max_pages") or 10),
        }
    query = action_spec.get("query")
    body = action_spec.get("body")
    return {
        "method": str(action_spec.get("method") or "GET").upper(),
        "path": str(action_spec.get("path") or "/"),
        "query": query if isinstance(query, dict) else {},
        "body": body,
        "unwrap": str(action_spec.get("unwrap") or ""),
        "pagination": pagination_shape,
        "mutating": bool(action_spec.get("mutating", False)),
        "max_response_bytes": int(
            action_spec.get("max_response_bytes")
            or connector_config.get("max_response_bytes")
            or 65536
        ),
    }


def http_action_shape_digest(shape: dict[str, Any]) -> str:
    canonical = json.dumps(shape, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_http_action_shape(
    *,
    connector_name: str,
    action: str,
    connector_contract: dict[str, Any],
    connector_config: dict[str, Any],
) -> str | None:
    expected_shapes = connector_contract.get("action_shapes")
    if not isinstance(expected_shapes, dict):
        return None
    expected_spec = expected_shapes.get(action)
    if not isinstance(expected_spec, dict):
        raise RExecOpValidationError(
            f"http action shape not declared: {connector_name}.{action}"
        )
    actions = connector_config.get("actions")
    actual_spec = actions.get(action) if isinstance(actions, dict) else None
    if not isinstance(actual_spec, dict):
        raise RExecOpValidationError(
            f"http action not configured: {connector_name}.{action}"
        )
    expected = canonical_http_action_shape(expected_spec, expected_spec)
    actual = canonical_http_action_shape(actual_spec, connector_config)
    if actual != expected:
        raise RExecOpValidationError(
            f"http action shape mismatch: {connector_name}.{action}"
        )
    return http_action_shape_digest(expected)
