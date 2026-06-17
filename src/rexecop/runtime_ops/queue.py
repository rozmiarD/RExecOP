from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from rexecop.storage.port import RuntimeStore


class RunNowQueue:
    """FIFO run-now queue for approved operations waiting on runtime capacity."""

    def __init__(self, store: RuntimeStore) -> None:
        self.store = store
        self.queue_dir = store.root / "queue"
        self.queue_file = self.queue_dir / "run_now.json"

    def _load(self) -> dict[str, Any]:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        if not self.queue_file.is_file():
            return {"pending": []}
        data = json.loads(self.queue_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"pending": []}
        pending = data.get("pending")
        if not isinstance(pending, list):
            pending = []
        return {"pending": [str(item) for item in pending]}

    def _save(self, data: dict[str, Any]) -> None:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.queue_file.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def list_pending(self) -> list[str]:
        return list(self._load()["pending"])

    def position(self, operation_id: str) -> int | None:
        pending = self.list_pending()
        if operation_id not in pending:
            return None
        return pending.index(operation_id)

    def enqueue(self, operation_id: str) -> int:
        data = self._load()
        pending = data["pending"]
        if operation_id not in pending:
            pending.append(operation_id)
        data["pending"] = pending
        data["updated_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
        self._save(data)
        return pending.index(operation_id)

    def remove(self, operation_id: str) -> None:
        data = self._load()
        pending = [item for item in data["pending"] if item != operation_id]
        data["pending"] = pending
        data["updated_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
        self._save(data)

    def peek(self) -> str | None:
        pending = self.list_pending()
        return pending[0] if pending else None

    def dequeue(self) -> str | None:
        data = self._load()
        pending = data["pending"]
        if not pending:
            return None
        operation_id = pending.pop(0)
        data["pending"] = pending
        data["updated_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
        self._save(data)
        return operation_id
