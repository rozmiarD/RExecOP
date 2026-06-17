from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rexecop.errors import RExecOpValidationError
from rexecop.operation.state import OperationState
from rexecop.storage.port import RuntimeStore

ACTIVE_LOCK_STATES = frozenset(
    {
        OperationState.APPROVED.value,
        OperationState.RUNNING.value,
        OperationState.PAUSED.value,
        OperationState.RESUMING.value,
        OperationState.RETRYING.value,
        OperationState.VALIDATING.value,
    }
)


def lock_filename(environment: str, target: str) -> str:
    safe_env = environment.replace("/", "_")
    safe_target = target.replace("/", "_")
    return f"{safe_env}__{safe_target}.lock"


class TargetLockManager:
    """Advisory per-(environment, target) lock backed by the file store."""

    def __init__(self, store: RuntimeStore) -> None:
        self.store = store
        self.locks_dir = store.root / "locks"

    def _path(self, environment: str, target: str) -> Path:
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        return self.locks_dir / lock_filename(environment, target)

    def read(self, environment: str, target: str) -> dict[str, Any] | None:
        path = self._path(environment, target)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None

    def holder_operation_id(self, environment: str, target: str) -> str | None:
        record = self.read(environment, target)
        if not record:
            return None
        return str(record.get("operation_id") or "") or None

    def is_stale(self, record: dict[str, Any]) -> bool:
        operation_id = str(record.get("operation_id") or "")
        if not operation_id:
            return True
        try:
            operation = self.store.load_operation(operation_id)
        except RExecOpValidationError:
            return True
        return operation.state not in ACTIVE_LOCK_STATES

    def acquire(self, *, environment: str, target: str, operation_id: str) -> bool:
        existing = self.read(environment, target)
        if existing and not self.is_stale(existing):
            return str(existing.get("operation_id")) == operation_id
        if existing and self.is_stale(existing):
            self.release(
                environment=environment,
                target=target,
                operation_id=str(existing.get("operation_id") or ""),
            )
        record = {
            "operation_id": operation_id,
            "environment": environment,
            "target": target,
            "acquired_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        }
        path = self._path(environment, target)
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return True

    def try_acquire(self, *, environment: str, target: str, operation_id: str) -> bool:
        existing = self.read(environment, target)
        if existing and not self.is_stale(existing):
            return str(existing.get("operation_id")) == operation_id
        return self.acquire(environment=environment, target=target, operation_id=operation_id)

    def release(self, *, environment: str, target: str, operation_id: str) -> None:
        existing = self.read(environment, target)
        if not existing:
            return
        if str(existing.get("operation_id") or "") not in {"", operation_id}:
            return
        path = self._path(environment, target)
        if path.is_file():
            path.unlink()
