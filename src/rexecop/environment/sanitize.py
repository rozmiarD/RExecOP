from __future__ import annotations

import re
from typing import Any

from rexecop.errors import RExecOpValidationError
from rexecop.evidence.redaction import SECRET_KEY_PATTERN, contains_strong_secret_pattern

FORBIDDEN_INLINE_SECRET_KEYS = re.compile(
    r"(password|passwd|secret|token|api_key|apikey|private_key|credential)$",
    re.IGNORECASE,
)
NON_SECRET_METADATA_KEYS = frozenset({"secrets_source"})


def sanitize_connectors_for_storage(connectors: dict[str, Any]) -> dict[str, Any]:
    """Persist connector config without inline secret material."""
    sanitized: dict[str, Any] = {}
    for name, config in connectors.items():
        if not isinstance(config, dict):
            raise RExecOpValidationError(f"connector config must be a mapping: {name}")
        sanitized[name] = _sanitize_mapping(config, path=name)
    return sanitized


def validate_no_inline_secrets(value: dict[str, Any]) -> None:
    _sanitize_mapping(value, path="environment")


def _sanitize_mapping(value: dict[str, Any], *, path: str) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        full_path = f"{path}.{key_text}"
        if isinstance(item, dict):
            cleaned[key_text] = _sanitize_mapping(item, path=full_path)
            continue
        if isinstance(item, list):
            cleaned[key_text] = [
                _sanitize_value(entry, path=f"{full_path}[]")
                for entry in item
            ]
            continue
        if _is_inline_secret_key(key_text) and not key_text.endswith("_ref"):
            if item not in (None, ""):
                raise RExecOpValidationError(
                    f"inline secret-like value forbidden at {full_path}; use secret_ref"
                )
        if isinstance(item, str) and contains_strong_secret_pattern(item):
            raise RExecOpValidationError(
                f"inline secret material forbidden at {full_path}; use secret_ref"
            )
        cleaned[key_text] = item
    return cleaned


def _sanitize_value(value: Any, *, path: str) -> Any:
    if isinstance(value, dict):
        return _sanitize_mapping(value, path=path)
    if isinstance(value, list):
        return [_sanitize_value(item, path=f"{path}[]") for item in value]
    if isinstance(value, str) and contains_strong_secret_pattern(value):
        raise RExecOpValidationError(
            f"inline secret material forbidden at {path}; use secret_ref"
        )
    return value


def _is_inline_secret_key(key: str) -> bool:
    if key.lower() in NON_SECRET_METADATA_KEYS:
        return False
    if key.endswith("_ref"):
        return False
    return bool(FORBIDDEN_INLINE_SECRET_KEYS.search(key) or SECRET_KEY_PATTERN.search(key))
