from __future__ import annotations

import json
from collections.abc import Mapping
from ipaddress import ip_address
from typing import Any
from urllib.parse import urljoin, urlparse

from rexecop.catalog.digest import canonical_digest
from rexecop.connectors import errors as connector_errors
from rexecop.errors import RExecOpUnsafeDestination
from rexecop.evidence.redaction import redact_payload, redact_text


def normalized_origin(url: str) -> tuple[str, str, int]:
    parsed = urlparse(str(url))
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("unsafe_destination")
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, host, port


def require_same_origin(expected_url: str, candidate_url: str) -> str:
    if normalized_origin(expected_url) != normalized_origin(candidate_url):
        raise ValueError("unsafe_destination")
    return candidate_url


def destination_binding(url: str) -> dict[str, Any]:
    scheme, host, port = normalized_origin(url)
    try:
        address = ip_address(host)
    except ValueError:
        address_class = "dns_name"
    else:
        if address.is_loopback:
            address_class = "loopback"
        elif address.is_link_local:
            address_class = "link_local"
        elif address.is_private:
            address_class = "private"
        else:
            address_class = "public_ip"
    return {
        "scheme": scheme,
        "effective_port": port,
        "address_class": address_class,
        "origin_binding_digest": "sha256:"
        + canonical_digest({"scheme": scheme, "host": host, "effective_port": port}),
    }


def validate_destination_posture(
    config: Mapping[str, Any],
    url: str,
    *,
    default_posture: str = "stable",
) -> dict[str, Any]:
    binding = destination_binding(url)
    declared = config.get("destination_binding")
    if isinstance(declared, Mapping) and dict(declared) != binding:
        raise RExecOpUnsafeDestination("resolved HTTP destination binding drift")
    posture = str(config.get("deployment_posture") or default_posture).strip().lower()
    if posture not in {"stable", "lab", "fixture"}:
        raise RExecOpUnsafeDestination(f"unsupported http deployment_posture: {posture}")
    if posture in {"lab", "fixture"}:
        return binding
    if binding["scheme"] != "https":
        raise RExecOpUnsafeDestination("stable http_api requires https")
    address_class = str(binding["address_class"])
    egress_enforced = bool(config.get("operator_egress_enforced"))
    dns_control = str(config.get("dns_rebinding_protection") or "").strip()
    if address_class == "dns_name" and not (egress_enforced and dns_control == "operator_egress"):
        raise RExecOpUnsafeDestination(
            "stable dns destination requires operator egress and DNS rebinding controls"
        )
    if address_class in {"private", "loopback", "link_local"} and not (
        egress_enforced and str(config.get("network_scope") or "") == "policy_bound"
    ):
        raise RExecOpUnsafeDestination(
            "stable private destination requires policy-bound operator egress"
        )
    return binding


def resolve_retry_config(
    connector_retry: Any,
    action_retry: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {}
    if isinstance(connector_retry, dict):
        base.update(connector_retry)
    if isinstance(action_retry, dict):
        base.update(action_retry)
    return base


def retry_delay_seconds(retry_cfg: dict[str, Any], attempt: int) -> float:
    base_delay = float(retry_cfg.get("base_delay") or 0.05)
    max_delay = float(retry_cfg.get("max_delay") or 0.2)
    return min(base_delay * (attempt + 1), max_delay)


def get_json_path(payload: Any, path: str) -> Any:
    current = payload
    for segment in str(path).split("."):
        if not segment:
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def resolve_next_url(base_url: str, current_url: str, next_value: Any) -> str | None:
    if next_value is None:
        return None
    next_text = str(next_value).strip()
    if not next_text:
        return None
    if next_text.startswith(("http://", "https://")):
        return next_text
    if next_text.startswith("/"):
        parsed = urlparse(current_url)
        return f"{parsed.scheme}://{parsed.netloc}{next_text}"
    return urljoin(f"{current_url.rstrip('/')}/", next_text)


def http_error_class(status_code: int) -> str:
    if status_code in {401, 403}:
        return connector_errors.AUTH_FAILED
    if status_code in {408, 429} or status_code >= 500:
        return connector_errors.TRANSIENT
    return connector_errors.VALIDATION_FAILED


def read_http_error_body(exc: Any, *, max_len: int = 200) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            snippet_obj = redact_payload(parsed)
        else:
            snippet_obj = redact_payload({"value": parsed})
        snippet = json.dumps(snippet_obj, ensure_ascii=True)
    except json.JSONDecodeError:
        snippet = redact_text(raw)
    return redact_text(snippet)[:max_len]


def merge_paginated_items(items_path: str, collected: list[Any]) -> dict[str, Any]:
    leaf = str(items_path).split(".")[-1] if items_path else "items"
    return {leaf: collected}
