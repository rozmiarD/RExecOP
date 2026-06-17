from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from rexecop.errors import RExecOpValidationError
from rexecop.operation.model import Operation
from rexecop.storage.factory import create_store, resolve_storage_backend
from rexecop.storage.sqlite_store import SqliteStore


def test_resolve_storage_backend_defaults_to_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REXECOP_STORAGE", raising=False)
    assert resolve_storage_backend() == "file"


def test_resolve_storage_backend_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REXECOP_STORAGE", "sqlite")
    assert resolve_storage_backend() == "sqlite"


def test_resolve_storage_backend_rejects_unknown() -> None:
    with pytest.raises(RExecOpValidationError, match="unsupported storage backend"):
        resolve_storage_backend("postgres")


def test_sqlite_store_uses_db_not_json_operations(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / ".rexecop")
    store.save_operation(
        Operation(
            id="op-sqlite",
            profile="p",
            environment="e",
            intent="i",
            target="t",
            mode="dry_run",
            state="planned",
            requested_by="test",
            created_at="2026-06-17T00:00:00+00:00",
            updated_at="2026-06-17T00:00:00+00:00",
        )
    )

    assert store.db_path.is_file()
    assert not (store.root / "operations" / "op-sqlite.json").is_file()

    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute("SELECT id FROM operations WHERE id = ?", ("op-sqlite",)).fetchone()
    assert row is not None


def test_create_store_factory(tmp_path: Path) -> None:
    sqlite_store = create_store(tmp_path / ".rexecop-a", backend="sqlite")
    file_store = create_store(tmp_path / ".rexecop-b", backend="file")
    assert isinstance(sqlite_store, SqliteStore)
    assert type(file_store).__name__ == "FileStore"


def test_sqlite_store_wal_mode(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / ".rexecop")
    store.ensure_layout()
    with sqlite3.connect(store.db_path) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()
    assert mode is not None
    assert str(mode[0]).lower() == "wal"


def test_sqlite_store_receipt_export_still_file_backed(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / ".rexecop")
    export = {"operation_id": "op-1", "summary": "ok"}
    path = store.save_receipt_export("op-1", export)
    assert path.is_file()
    assert store.load_receipt_export("op-1") == export
    assert json.loads(path.read_text(encoding="utf-8")) == export
