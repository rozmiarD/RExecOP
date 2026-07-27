from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

from rexecop.catalog.digest import canonical_digest
from rexecop.environment.loader import (
    _load_environment_for_secret_inspection,
    load_environment,
)
from rexecop.environment.model import Environment
from rexecop.environment.sanitize import validate_no_inline_secrets
from rexecop.errors import RExecOpValidationError
from rexecop.profile.loader import load_profile
from rexecop.profile.resolver import resolve_profile_path
from rexecop.yaml_input import load_yaml_file

MAX_CATALOG_BYTES = 1024 * 1024
MAX_TARGETS = 1024
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
CATALOG_KEYS = frozenset({"version", "targets"})
TARGET_KEYS = frozenset(
    {
        "id",
        "target_kind",
        "profile_ref",
        "environment_ref",
        "environment_target",
        "capabilities",
        "connector_refs",
        "classification",
    }
)


def load_catalog_document(path: Path) -> tuple[str, list[dict[str, Any]], str]:
    version, entries, digest, _ = _load_catalog_document(
        path,
        inspect_secret_ref_collisions=False,
    )
    return version, entries, digest


def _load_catalog_environments_for_secret_inspection(
    path: Path,
) -> list[tuple[Path, Environment]]:
    _, _, _, environments = _load_catalog_document(
        path,
        inspect_secret_ref_collisions=True,
    )
    return sorted(environments.items(), key=lambda item: str(item[0]))


def _load_catalog_document(
    path: Path,
    *,
    inspect_secret_ref_collisions: bool,
) -> tuple[str, list[dict[str, Any]], str, dict[Path, Environment]]:
    resolved = path.expanduser().resolve()
    _validate_catalog_file(resolved)
    document = load_yaml_file(resolved, max_bytes=MAX_CATALOG_BYTES)
    if not isinstance(document, dict):
        raise RExecOpValidationError("target_catalog document must be a mapping")
    validate_no_inline_secrets(document)
    raw = document.get("target_catalog")
    if not isinstance(raw, dict):
        raise RExecOpValidationError("target_catalog mapping required")
    _strict_keys(raw, CATALOG_KEYS, "target_catalog")
    version = _token(raw.get("version"), "target_catalog.version")
    if version != "0.1":
        raise RExecOpValidationError(f"unsupported target catalog version: {version}")
    targets = raw.get("targets")
    if not isinstance(targets, list) or not targets:
        raise RExecOpValidationError("target_catalog.targets must be a non-empty list")
    if len(targets) > MAX_TARGETS:
        raise RExecOpValidationError("target catalog exceeds target limit")
    entries: list[dict[str, Any]] = []
    environments: dict[Path, Environment] = {}
    seen: set[str] = set()
    for index, item in enumerate(targets):
        if not isinstance(item, dict):
            raise RExecOpValidationError(f"target catalog entry {index} must be a mapping")
        _strict_keys(item, TARGET_KEYS, f"target_catalog.targets[{index}]")
        normalized = _normalize_target_entry(
            item,
            resolved.parent,
            index,
            environments=environments,
            inspect_secret_ref_collisions=inspect_secret_ref_collisions,
        )
        target_id = str(normalized["id"])
        if target_id in seen:
            raise RExecOpValidationError(f"duplicate target catalog id: {target_id}")
        seen.add(target_id)
        entries.append(normalized)
    if not inspect_secret_ref_collisions:
        from rexecop.secrets.reference import (
            enforce_secret_ref_env_collision_freedom,
        )

        enforce_secret_ref_env_collision_freedom(
            {
                "environments": [
                    environment.as_dict()
                    for _, environment in sorted(
                        environments.items(), key=lambda item: str(item[0])
                    )
                ]
            }
        )
    return version, entries, canonical_digest(raw), environments


def _normalize_target_entry(
    item: dict[str, Any],
    catalog_dir: Path,
    index: int,
    *,
    environments: dict[Path, Environment],
    inspect_secret_ref_collisions: bool,
) -> dict[str, Any]:
    prefix = f"target_catalog.targets[{index}]"
    target_id = _token(item.get("id"), f"{prefix}.id")
    target_kind = _token(item.get("target_kind"), f"{prefix}.target_kind")
    profile_ref = _text(item.get("profile_ref"), f"{prefix}.profile_ref")
    environment_ref = _text(item.get("environment_ref"), f"{prefix}.environment_ref")
    environment_target = _token(
        item.get("environment_target") or target_id,
        f"{prefix}.environment_target",
    )
    capabilities = _token_list(item.get("capabilities"), f"{prefix}.capabilities")
    connector_refs = _token_list(item.get("connector_refs"), f"{prefix}.connector_refs")
    classification = _classification(item.get("classification"), f"{prefix}.classification")

    environment_path = _resolve_file(catalog_dir, environment_ref, f"{prefix}.environment_ref")
    if Path(profile_ref).is_absolute() or profile_ref.startswith("."):
        profile_input: str | Path = _resolve_path(catalog_dir, profile_ref)
    else:
        profile_input = profile_ref
    resolved_profile_path = resolve_profile_path(profile_input).resolve()
    profile = load_profile(resolved_profile_path)
    profile_path = profile.root.resolve()
    environment = environments.get(environment_path)
    if environment is None:
        environment = (
            _load_environment_for_secret_inspection(environment_path)
            if inspect_secret_ref_collisions
            else load_environment(environment_path)
        )
        environments[environment_path] = environment
    if environment.profile and environment.profile != profile.name:
        raise RExecOpValidationError(
            f"catalog environment profile mismatch for target {target_id}"
        )
    target_spec = environment.targets.get(environment_target)
    if not isinstance(target_spec, dict):
        raise RExecOpValidationError(
            f"catalog environment target not found: {target_id}"
        )
    actual_kind = str(target_spec.get("type") or "host").strip()
    if actual_kind != target_kind:
        raise RExecOpValidationError(
            f"catalog target kind mismatch for {target_id}: "
            f"expected {actual_kind}, got {target_kind}"
        )
    for connector_ref in connector_refs:
        config = environment.connectors.get(connector_ref)
        if not isinstance(config, dict):
            raise RExecOpValidationError(
                f"catalog connector not configured for {target_id}: {connector_ref}"
            )
        if not bool(config.get("enabled", True)):
            raise RExecOpValidationError(
                f"catalog connector disabled for {target_id}: {connector_ref}"
            )
    return {
        "id": target_id,
        "target_kind": target_kind,
        "profile_ref": profile.name,
        "environment_id": environment.id,
        "environment_target": environment_target,
        "capabilities": capabilities,
        "connector_refs": connector_refs,
        "classification": classification,
        "environment_path": environment_path,
        "profile_path": profile_path,
    }


def _validate_catalog_file(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise RExecOpValidationError(f"target catalog not found: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RExecOpValidationError("target catalog must be a regular file")
    if info.st_uid != os.getuid():
        raise RExecOpValidationError("target catalog must be owned by the current user")
    if info.st_size > MAX_CATALOG_BYTES:
        raise RExecOpValidationError("target catalog exceeds size limit")


def _strict_keys(value: dict[str, Any], allowed: frozenset[str], path: str) -> None:
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise RExecOpValidationError(f"unknown fields at {path}: {', '.join(unknown)}")


def _token(value: Any, path: str) -> str:
    text = _text(value, path)
    if not TOKEN.fullmatch(text):
        raise RExecOpValidationError(f"invalid catalog token at {path}")
    return text


def _text(value: Any, path: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 4096:
        raise RExecOpValidationError(f"non-empty bounded text required at {path}")
    return text


def _token_list(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RExecOpValidationError(f"list required at {path}")
    items = tuple(sorted({_token(item, f"{path}[]") for item in value}))
    if len(items) != len(value):
        raise RExecOpValidationError(f"duplicate values forbidden at {path}")
    return items


def _classification(value: Any, path: str) -> dict[str, str | int | float | bool]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 32:
        raise RExecOpValidationError(f"bounded mapping required at {path}")
    result: dict[str, str | int | float | bool] = {}
    for key, item in value.items():
        name = _token(key, f"{path}.key")
        if not isinstance(item, (str, int, float, bool)) or len(str(item)) > 256:
            raise RExecOpValidationError(f"scalar value required at {path}.{name}")
        result[name] = item
    return result


def _resolve_path(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _resolve_file(base: Path, value: str, field: str) -> Path:
    path = _resolve_path(base, value)
    if not path.is_file():
        raise RExecOpValidationError(f"catalog referenced file not found at {field}")
    return path
