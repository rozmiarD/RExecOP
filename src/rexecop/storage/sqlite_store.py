from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rexecop.errors import RExecOpConcurrencyConflict, RExecOpValidationError
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.storage.atomic import FILE_MODE, secure_directory, secure_file
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
        secure_directory(self.db_path.parent)

    def _init_schema(self) -> None:
        self.ensure_layout()
        with self._connection() as conn:
            conn.executescript(_SCHEMA_SQL)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )

    @contextmanager
    def _connection(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        self.ensure_layout()
        if not self.db_path.exists():
            try:
                descriptor = os.open(
                    self.db_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    FILE_MODE,
                )
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
        secure_file(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
            self._secure_database_files()
            yield conn
            conn.commit()
        finally:
            self._secure_database_files()
            conn.close()
            self._secure_database_files()

    def _secure_database_files(self) -> None:
        for path in (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-shm"),
        ):
            if path.exists():
                secure_file(path)

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

    def acquire_execution_lease(self, *, worker_id: str) -> dict[str, Any]:
        return self._files.acquire_execution_lease(worker_id=worker_id)

    def renew_execution_lease(self, lease: dict[str, Any]) -> dict[str, Any]:
        return self._files.renew_execution_lease(lease)

    def release_execution_lease(self, lease: dict[str, Any]) -> bool:
        return self._files.release_execution_lease(lease)

    def validate_execution_lease(self, lease: dict[str, Any]) -> None:
        self._files.validate_execution_lease(lease)

    def queue_list_pending(self) -> list[str]:
        return self._files.queue_list_pending()

    def queue_position(self, operation_id: str) -> int | None:
        return self._files.queue_position(operation_id)

    def queue_enqueue(self, operation_id: str) -> int:
        return self._files.queue_enqueue(operation_id)

    def queue_remove(self, operation_id: str) -> None:
        self._files.queue_remove(operation_id)

    def queue_discard_pending(self, operation_id: str) -> None:
        self._files.queue_discard_pending(operation_id)

    def queue_claim(self, lease: dict[str, Any]) -> dict[str, Any] | None:
        return self._files.queue_claim(lease)

    def queue_complete_claim(self, operation_id: str, lease: dict[str, Any]) -> None:
        self._files.queue_complete_claim(operation_id, lease)

    def start_execution_attempt(self, **binding: Any) -> dict[str, Any]:
        return self._files.start_execution_attempt(**binding)

    def allocate_execution_attempt_id(self) -> str:
        return self._files.allocate_execution_attempt_id()

    def finish_execution_attempt(
        self,
        attempt: dict[str, Any],
        *,
        status: str,
        result_digest: str = "",
        error_class: str = "",
    ) -> dict[str, Any]:
        return self._files.finish_execution_attempt(
            attempt,
            status=status,
            result_digest=result_digest,
            error_class=error_class,
        )

    def recover_started_attempts(self) -> list[str]:
        return self._files.recover_started_attempts()

    def has_indeterminate_side_effect(self, operation_id: str) -> bool:
        return self._files.has_indeterminate_side_effect(operation_id)

    def list_pending_projection_operations(self) -> list[Operation]:
        return [
            operation
            for operation in self.list_operations()
            if isinstance(operation.metadata.get("sclite_projection"), dict)
            and operation.metadata["sclite_projection"].get("status") == "pending"
        ]

    def save_execution_permit(self, permit: dict[str, Any]) -> Path:
        return self._files.save_execution_permit(permit)

    def load_execution_permit(self, operation_id: str, step_id: str) -> dict[str, Any]:
        return self._files.load_execution_permit(operation_id, step_id)

    def load_execution_permit_for_attempt(
        self,
        operation_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        return self._files.load_execution_permit_for_attempt(operation_id, attempt_id)

    def claim_governance_decision_once(self, **claim: Any) -> bool:
        return self._files.claim_governance_decision_once(**claim)

    def save_operation(self, operation: Operation) -> None:
        with self._connection(immediate=True) as conn:
            row = conn.execute(
                "SELECT payload FROM operations WHERE id = ?", (operation.id,)
            ).fetchone()
            current_revision = 0
            if row is not None:
                current_revision = int(json.loads(str(row[0])).get("operation_revision") or 0)
            if current_revision != operation.operation_revision:
                raise RExecOpConcurrencyConflict(
                    f"concurrency_conflict: operation {operation.id} expected revision "
                    f"{operation.operation_revision}, found {current_revision}"
                )
            operation.operation_revision = current_revision + 1
            payload = json.dumps(operation.as_dict(), sort_keys=True)
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

    def save_structured_log_event(self, event: dict[str, Any]) -> None:
        self._files.save_structured_log_event(event)

    def list_structured_log_events(
        self,
        *,
        operation_id: str | None = None,
        correlation_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self._files.list_structured_log_events(
            operation_id=operation_id,
            correlation_id=correlation_id,
            limit=limit,
        )
