from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

from rexecop.errors import RExecOpValidationError

SECRET_REF_ENV_COLLISION_MESSAGE = "secret_ref environment key collision"
SUGGESTION_AMBIGUITY_MESSAGE = (
    "generated secret_ref suggestions are ambiguous; use explicit refs"
)
_SECRET_REF_KEYS = frozenset({"secret_ref"})
_MAX_DIAGNOSTIC_GROUPS = 32
_MAX_DIAGNOSTIC_ITEMS = 64
_MAX_REF_CHARS = 256
_MAX_PATH_CHARS = 512


class _SecretRefEnvCollision(RExecOpValidationError):
    """Private terminal failure for distinct refs sharing one legacy env key."""


class _EnvKeyClaims:
    """Per-resolver process-local ownership of legacy environment keys."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claims: dict[str, str] = {}

    def claim(self, secret_ref: str) -> tuple[str, str]:
        ref = secret_ref.strip()
        if not ref:
            raise RExecOpValidationError("secret_ref is required")
        env_key = env_key_for_secret_ref(ref)
        with self._lock:
            owner = self._claims.get(env_key)
            if owner is not None and owner != ref:
                raise _SecretRefEnvCollision(SECRET_REF_ENV_COLLISION_MESSAGE)
            self._claims[env_key] = ref
        return ref, env_key


def collect_secret_ref_bindings(
    value: Any,
    *,
    path: str = "",
) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text in _SECRET_REF_KEYS or key_text.endswith("_secret_ref"):
                bindings.append({"path": child_path, "ref": str(item or "").strip()})
            bindings.extend(collect_secret_ref_bindings(item, path=child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child_path = f"{path}[{index}]"
            bindings.extend(collect_secret_ref_bindings(item, path=child_path))
    return bindings


def env_key_for_secret_ref(secret_ref: str) -> str:
    ref = secret_ref.strip()
    return f"REXECOP_SECRET_{ref.upper().replace('-', '_')}"


def secret_ref_env_collisions(
    bindings: list[dict[str, str]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for binding in bindings:
        ref = str(binding.get("ref") or "").strip()
        if not ref:
            continue
        path = str(binding.get("path") or "")
        document = str(binding.get("document") or "")
        location = f"{document}:{path}" if document else path
        grouped[env_key_for_secret_ref(ref)][ref].append(location)

    collisions: list[dict[str, Any]] = []
    for env_key in sorted(grouped):
        refs = grouped[env_key]
        if len(refs) < 2:
            continue
        all_paths = sorted(path for paths in refs.values() for path in paths)
        collisions.append(
            {
                "env_key": _bounded(env_key, _MAX_REF_CHARS),
                "refs": [
                    _bounded(ref, _MAX_REF_CHARS)
                    for ref in sorted(refs)[:_MAX_DIAGNOSTIC_ITEMS]
                ],
                "paths": [
                    _bounded(path, _MAX_PATH_CHARS)
                    for path in all_paths[:_MAX_DIAGNOSTIC_ITEMS]
                ],
                "ref_count": len(refs),
                "binding_count": len(all_paths),
            }
        )
        if len(collisions) >= _MAX_DIAGNOSTIC_GROUPS:
            break
    return collisions


def enforce_secret_ref_env_collision_freedom(value: Any) -> None:
    if secret_ref_env_collisions(collect_secret_ref_bindings(value)):
        raise _SecretRefEnvCollision(SECRET_REF_ENV_COLLISION_MESSAGE)


def canonicalize_suggested_ref(value: str) -> str:
    return "_".join(
        part for part in value.strip().lower().replace("-", "_").split("_") if part
    )


def validate_suggestion_identities(
    existing_refs: list[dict[str, str]],
    suggestions: list[dict[str, str]],
) -> None:
    generated_bindings = [
        {
            "path": str(item.get("path") or ""),
            "ref": str(item.get("suggested_ref") or "").strip(),
        }
        for item in suggestions
    ]
    generated_refs = [item["ref"] for item in generated_bindings]
    existing_env_keys = {
        env_key_for_secret_ref(str(item.get("ref") or "").strip())
        for item in existing_refs
        if str(item.get("ref") or "").strip()
    }
    generated_overlaps_existing = any(
        binding["ref"]
        and env_key_for_secret_ref(binding["ref"]) in existing_env_keys
        for binding in generated_bindings
    )
    if (
        len(generated_refs) != len(set(generated_refs))
        or secret_ref_env_collisions(generated_bindings)
        or generated_overlaps_existing
    ):
        raise RExecOpValidationError(SUGGESTION_AMBIGUITY_MESSAGE)


def _bounded(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    return value[: maximum - 3] + "..."
