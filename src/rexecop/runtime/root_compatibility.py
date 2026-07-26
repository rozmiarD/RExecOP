"""Compatibility policy for persisted runtime roots."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rexecop.errors import RExecOpValidationError

RUNTIME_ROOT_COMPATIBILITY_SCHEMA = "rexecop.runtime_root_compatibility.v1"
RUNTIME_ROOT_UPGRADE_POLICY = "alpha_root_requires_new_v1_root"
SUPPORTED_RUNTIME_MANIFEST_SCHEMA = "rexecop.runtime_init.v0.1"
RUNTIME_MANIFEST_FILENAME = "runtime_manifest.json"
RUNTIME_MANIFEST_MAX_BYTES = 64 * 1024
SUPPORTED_RUNTIME_STORAGE_BACKENDS = frozenset({"file", "sqlite"})

_VERSION_MAJOR = re.compile(r"^(\d+)(?:[.]|$)")


def _major(version: object) -> int | None:
    match = _VERSION_MAJOR.match(str(version or "").strip())
    return int(match.group(1)) if match else None


def runtime_root_compatibility(
    manifest: object,
    *,
    target_version: str,
    configured_storage_backend: str | None = None,
    manifest_present: bool = True,
    root_state: str | None = None,
) -> dict[str, Any]:
    observed_root_state = root_state or ("present" if manifest_present else "absent")
    payload = manifest if isinstance(manifest, Mapping) else {}
    stored_version = str(payload.get("rexecop_version") or "").strip()
    manifest_schema = str(payload.get("schema") or "").strip()
    stored_storage_backend = str(payload.get("storage_backend") or "").strip().lower()
    configured_backend = str(configured_storage_backend or "").strip().lower()
    stored_major = _major(stored_version)
    target_major = _major(target_version)

    reason_code = "runtime_root_compatible"
    status = "compatible"
    guidance = "runtime root may be opened with the configured storage backend"
    if observed_root_state == "invalid":
        status = "blocked"
        reason_code = "runtime_root_path_invalid"
        guidance = "select a real runtime root with no symbolic-link path components"
    elif not manifest_present and observed_root_state == "nonempty":
        status = "blocked"
        reason_code = "runtime_root_manifest_missing_nonempty"
        guidance = "select an absent or strictly empty root and run rexecop init"
    elif not manifest_present:
        status = "blocked"
        reason_code = "runtime_root_manifest_missing"
        guidance = "run rexecop init explicitly for this runtime root"
    elif not isinstance(manifest, Mapping):
        status = "blocked"
        reason_code = "runtime_root_manifest_invalid"
        guidance = "use a verified backup or initialize a new runtime root"
    elif manifest_schema != SUPPORTED_RUNTIME_MANIFEST_SCHEMA:
        status = "blocked"
        reason_code = "runtime_root_manifest_schema_unsupported"
        guidance = "use a compatible RExecOP binary or initialize a new runtime root"
    elif stored_major is None or target_major is None:
        status = "blocked"
        reason_code = "runtime_root_version_invalid"
        guidance = "use a verified backup or initialize a new runtime root"
    elif stored_major == 0 and target_major >= 1:
        status = "new_root_required"
        reason_code = "runtime_root_new_root_required"
        guidance = "initialize a new v1 runtime root; alpha roots are not upgraded in place"
    elif stored_major > target_major:
        status = "blocked"
        reason_code = "runtime_root_downgrade_unsupported"
        guidance = "use a compatible newer RExecOP binary; downgrade is not supported"
    elif stored_major != target_major:
        status = "blocked"
        reason_code = "runtime_root_major_version_unsupported"
        guidance = "use a binary matching the root major or initialize a new runtime root"
    elif stored_storage_backend not in SUPPORTED_RUNTIME_STORAGE_BACKENDS:
        status = "blocked"
        reason_code = "runtime_root_storage_backend_invalid"
        guidance = "use a verified backup or initialize a new runtime root"
    elif configured_backend and stored_storage_backend != configured_backend:
        status = "blocked"
        reason_code = "runtime_root_storage_backend_mismatch"
        guidance = (
            f"set REXECOP_STORAGE={stored_storage_backend} or initialize a new "
            f"{configured_backend} runtime root; backend conversion is not supported"
        )

    return {
        "schema": RUNTIME_ROOT_COMPATIBILITY_SCHEMA,
        "status": status,
        "reason_code": reason_code,
        "policy": RUNTIME_ROOT_UPGRADE_POLICY,
        "manifest_schema": manifest_schema,
        "stored_version": stored_version,
        "target_version": target_version,
        "stored_storage_backend": stored_storage_backend,
        "configured_storage_backend": configured_backend,
        "root_state": observed_root_state,
        "in_place_upgrade_supported": status == "compatible",
        "new_root_required": status == "new_root_required",
        "guidance": guidance,
    }


def require_runtime_root_compatible(
    manifest: object,
    *,
    target_version: str,
    configured_storage_backend: str | None = None,
    manifest_present: bool = True,
    root_state: str | None = None,
) -> dict[str, Any]:
    decision = runtime_root_compatibility(
        manifest,
        target_version=target_version,
        configured_storage_backend=configured_storage_backend,
        manifest_present=manifest_present,
        root_state=root_state,
    )
    return require_runtime_root_decision(decision)


def require_runtime_root_decision(decision: Mapping[str, Any]) -> dict[str, Any]:
    if decision["status"] != "compatible":
        raise RExecOpValidationError(str(decision["reason_code"]))
    return dict(decision)


def inspect_runtime_root_compatibility(
    root: Path,
    *,
    target_version: str,
    configured_storage_backend: str | None = None,
) -> dict[str, Any]:
    """Inspect one persisted root manifest without creating or changing the root."""

    manifest_path = root / RUNTIME_MANIFEST_FILENAME
    root_descriptor, root_state = _open_runtime_root(root)
    if root_descriptor is None:
        decision = runtime_root_compatibility(
            None,
            target_version=target_version,
            configured_storage_backend=configured_storage_backend,
            manifest_present=False,
            root_state=root_state,
        )
    else:
        try:
            manifest, manifest_present, observed_root_state = _read_runtime_manifest(
                root_descriptor
            )
            decision = runtime_root_compatibility(
                manifest,
                target_version=target_version,
                configured_storage_backend=configured_storage_backend,
                manifest_present=manifest_present,
                root_state=observed_root_state,
            )
        finally:
            os.close(root_descriptor)
    return {**decision, "manifest_path": str(manifest_path)}


def _open_runtime_root(root: Path) -> tuple[int | None, str]:
    absolute_root = Path(os.path.abspath(root))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(absolute_root.anchor, flags)
    try:
        for part in absolute_root.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                os.close(descriptor)
                return None, "absent"
            except OSError:
                os.close(descriptor)
                return None, "invalid"
            os.close(descriptor)
            descriptor = child
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, "present"


def _read_runtime_manifest(root_descriptor: int) -> tuple[object, bool, str]:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(
            RUNTIME_MANIFEST_FILENAME,
            flags,
            dir_fd=root_descriptor,
        )
    except FileNotFoundError:
        try:
            root_state = "empty" if not os.listdir(root_descriptor) else "nonempty"
        except OSError:
            return None, False, "invalid"
        return None, False, root_state
    except OSError:
        return None, True, "present"

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > RUNTIME_MANIFEST_MAX_BYTES:
            return None, True, "present"
        data = bytearray()
        while len(data) <= RUNTIME_MANIFEST_MAX_BYTES:
            chunk = os.read(
                descriptor,
                min(65536, RUNTIME_MANIFEST_MAX_BYTES + 1 - len(data)),
            )
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > RUNTIME_MANIFEST_MAX_BYTES:
            return None, True, "present"
    except OSError:
        return None, True, "present"
    finally:
        os.close(descriptor)

    try:
        manifest = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeError, ValueError):
        manifest = None
    return manifest, True, "present"


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate runtime manifest key")
        result[key] = value
    return result


__all__ = [
    "RUNTIME_ROOT_COMPATIBILITY_SCHEMA",
    "RUNTIME_ROOT_UPGRADE_POLICY",
    "RUNTIME_MANIFEST_FILENAME",
    "RUNTIME_MANIFEST_MAX_BYTES",
    "SUPPORTED_RUNTIME_MANIFEST_SCHEMA",
    "SUPPORTED_RUNTIME_STORAGE_BACKENDS",
    "inspect_runtime_root_compatibility",
    "require_runtime_root_compatible",
    "require_runtime_root_decision",
    "runtime_root_compatibility",
]
