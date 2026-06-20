from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin, urlparse

from rexecop.connectors import errors as connector_errors
from rexecop.evidence.redaction import redact_payload


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
        snippet = raw
    return snippet[:max_len]


def merge_paginated_items(items_path: str, collected: list[Any]) -> dict[str, Any]:
    leaf = str(items_path).split(".")[-1] if items_path else "items"
    return {leaf: collected}
