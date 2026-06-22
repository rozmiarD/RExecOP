from __future__ import annotations

import re
from threading import RLock
from typing import Any

SECRET_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|token|api_key|apikey|private_key|credential|"
    r"authorization|auth_header)",
    re.IGNORECASE,
)

REDACTED = "[REDACTED]"

_MIN_SECRET_LENGTH = 4
_REGISTERED_SECRET_VALUES: set[str] = set()
_REGISTERED_SECRET_VALUES_LOCK = RLock()

_TEXT_SECRET_PATTERNS = (
    re.compile(
        r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----.*?"
        r"-----END (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{4,}"),
    re.compile(r"(?i)https?://[^\s/@:]{1,}:[^\s/@]{1,}@"),
    re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
        r"private[_-]?key|client[_-]?secret|authorization)\b\s*[:=]\s*"
        r"[\"']?[^\s\"',;}\]]{4,}"
    ),
)


def register_secret_value(value: str) -> None:
    """Register a resolved secret for exact-value redaction within this process."""
    text = str(value)
    if len(text) < _MIN_SECRET_LENGTH:
        return
    with _REGISTERED_SECRET_VALUES_LOCK:
        _REGISTERED_SECRET_VALUES.add(text)


def clear_registered_secret_values() -> None:
    """Clear process-local values; intended for test and worker lifecycle isolation."""
    with _REGISTERED_SECRET_VALUES_LOCK:
        _REGISTERED_SECRET_VALUES.clear()


def redact_text(value: str) -> str:
    redacted = str(value)
    with _REGISTERED_SECRET_VALUES_LOCK:
        registered = sorted(_REGISTERED_SECRET_VALUES, key=len, reverse=True)
    for secret in registered:
        redacted = redacted.replace(secret, REDACTED)
    for pattern in _TEXT_SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def contains_strong_secret_pattern(value: str) -> bool:
    text = str(value)
    return any(pattern.search(text) for pattern in _TEXT_SECRET_PATTERNS[:-1])


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if SECRET_KEY_PATTERN.search(str(key)):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_payload(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value
