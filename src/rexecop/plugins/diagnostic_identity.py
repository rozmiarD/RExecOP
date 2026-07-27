from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal, TypeAlias

IdentityKind: TypeAlias = Literal[
    "entry",
    "profile",
    "distribution",
    "action",
    "exception",
]

_NAME_LIMIT = 96
_EXCEPTION_LIMIT = 64
_DIGEST_PREFIX = "sha256:"
_DISPLAY_DIGEST_LENGTH = 12
_ENTRY_IDENTITY_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
_ACTION_IDENTITY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
_EXCEPTION_IDENTITY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True, slots=True)
class ProjectedDiagnosticIdentity:
    kind: IdentityKind
    display: str
    full_digest: str


def project_diagnostic_identity(
    raw: object,
    *,
    kind: IdentityKind,
) -> ProjectedDiagnosticIdentity:
    """Create one terminal, bounded diagnostic projection of an identity."""
    neutral, limit, grammar = _projection_rules(kind)
    conversion_failed = False
    try:
        text = raw if isinstance(raw, str) else str(raw)
    except Exception:  # noqa: BLE001 - conversion is an untrusted diagnostic boundary
        text = neutral
        conversion_failed = True

    digest = hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()
    full_digest = _DIGEST_PREFIX + digest
    safe = not conversion_failed and grammar.fullmatch(text) is not None
    if safe and len(text) <= limit:
        display = text
    else:
        suffix = "~" + digest[:_DISPLAY_DIGEST_LENGTH]
        display = text[: limit - len(suffix)] + suffix if safe else neutral + suffix
    return ProjectedDiagnosticIdentity(
        kind=kind,
        display=display,
        full_digest=full_digest,
    )


def _projection_rules(kind: IdentityKind) -> tuple[str, int, re.Pattern[str]]:
    if kind == "action":
        return "action", _NAME_LIMIT, _ACTION_IDENTITY_RE
    if kind == "exception":
        return "Exception", _EXCEPTION_LIMIT, _EXCEPTION_IDENTITY_RE
    return "unknown", _NAME_LIMIT, _ENTRY_IDENTITY_RE
