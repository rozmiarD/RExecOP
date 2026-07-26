from __future__ import annotations

import os
from pathlib import Path

from rexecop import __version__
from rexecop.errors import RExecOpValidationError
from rexecop.runtime.root import resolve_runtime_root
from rexecop.runtime.root_compatibility import (
    inspect_runtime_root_compatibility,
    require_runtime_root_decision,
)
from rexecop.storage.file_store import FileStore
from rexecop.storage.port import RuntimeStore
from rexecop.storage.sqlite_store import SqliteStore

SUPPORTED_STORAGE_BACKENDS = frozenset({"file", "sqlite"})


def resolve_storage_backend(explicit: str | None = None) -> str:
    backend = (explicit or os.environ.get("REXECOP_STORAGE") or "file").strip().lower()
    if backend not in SUPPORTED_STORAGE_BACKENDS:
        raise RExecOpValidationError(
            f"unsupported storage backend: {backend!r} (expected file or sqlite)"
        )
    return backend


def create_store(
    root: Path | None = None,
    *,
    backend: str | None = None,
) -> RuntimeStore:
    resolved = resolve_storage_backend(backend)
    runtime_root = root or resolve_runtime_root()
    decision = inspect_runtime_root_compatibility(
        runtime_root,
        target_version=__version__,
        configured_storage_backend=resolved,
    )
    require_runtime_root_decision(decision)
    return _construct_store(runtime_root, resolved)


def _create_store_for_init(root: Path, *, backend: str) -> RuntimeStore:
    decision = inspect_runtime_root_compatibility(
        root,
        target_version=__version__,
        configured_storage_backend=backend,
    )
    if decision["reason_code"] != "runtime_root_manifest_missing":
        require_runtime_root_decision(decision)
    return _construct_store(root, backend)


def _construct_store(runtime_root: Path, backend: str) -> RuntimeStore:
    if backend == "sqlite":
        return SqliteStore(root=runtime_root)
    return FileStore(root=runtime_root)
