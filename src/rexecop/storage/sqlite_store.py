from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rexecop.errors import RExecOpValidationError
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.storage.file_store import FileStore

_SCHEMA_VERSION = 1
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS operations (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    operation_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_events (
    operation_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (operation_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_evidence_events_operation
    ON evidence_events (operation_id);
"""


class SqliteStore:
    """SQLite-backed operations/plans/evidence with file layout for SCLite and runtime aux."""

    def __init__(self, root: Path | None = None, db_path: Path | None = None) -> None:
        self._files = FileStore(root)
        self.root = self._files.root
        self.db_path = db_path or self.root / "rexecop.db"
        self._init_schema()

    def ensure_layout(self) -> None:
        self._files.ensure_layout()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_schema(self) -> None:
        self.ensure_layout()
        with self._connection() as conn:
            conn.executescript(_SCHEMA_SQL)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.ensure_layout()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def operation_sclite_dir(self, operation_id: str) -> Path:
        return self._files.operation_sclite_dir(operation_id)

    def save_receipt_export(self, operation_id: str, export: dict[str, Any]) -> Path:
        return self._files.save_receipt_export(operation_id, export)

    def load_receipt_export(self, operation_id: str) -> dict[str, Any]:
        return self._files.load_receipt_export(operation_id)

    def save_approval(self, operation_id: str, approval: dict[str, Any]) -> Path:
        return self._files.save_approval(operation_id, approval)

    def load_approval(self, operation_id: str) -> dict[str, Any]:
        return self._files.load_approval(operation_id)

    def save_operation(self, operation: Operation) -> None:
        payload = json.dumps(operation.as_dict(), sort_keys=True)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO operations (id, payload) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                (operation.id, payload),
            )

    def load_operation(self, operation_id: str) -> Operation:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload FROM operations WHERE id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise RExecOpValidationError(f"operation not found: {operation_id}")
        return Operation.from_dict(json.loads(str(row[0])))

    def list_operations(self) -> list[Operation]:
        with self._connection() as conn:
            rows = conn.execute("SELECT payload FROM operations ORDER BY id").fetchall()
        return [Operation.from_dict(json.loads(str(row[0]))) for row in rows]

    def save_plan(self, plan: OperationPlan) -> None:
        payload = json.dumps(plan.as_dict(), sort_keys=True)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO plans (operation_id, payload) VALUES (?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET payload = excluded.payload
                """,
                (plan.operation_id, payload),
            )

    def load_plan(self, operation_id: str) -> OperationPlan:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload FROM plans WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise RExecOpValidationError(f"operation plan not found: {operation_id}")
        return OperationPlan.from_dict(json.loads(str(row[0])))

    def save_evidence_event(self, operation_id: str, event: dict[str, Any]) -> None:
        event_id = str(event["event_id"])
        payload = json.dumps(event, sort_keys=True)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO evidence_events (operation_id, event_id, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(operation_id, event_id) DO UPDATE SET payload = excluded.payload
                """,
                (operation_id, event_id, payload),
            )

    def list_evidence_events(self, operation_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM evidence_events
                WHERE operation_id = ?
                ORDER BY event_id
                """,
                (operation_id,),
            ).fetchall()
        return [json.loads(str(row[0])) for row in rows]
