from __future__ import annotations

import os
from pathlib import Path

from rexecop.errors import RExecOpValidationError
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


def create_store(root: Path | None = None, *, backend: str | None = None) -> RuntimeStore:
    resolved = resolve_storage_backend(backend)
    if resolved == "sqlite":
        return SqliteStore(root=root)
    return FileStore(root=root)
