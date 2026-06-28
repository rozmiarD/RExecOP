from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sclite.integrity import artifact_descriptor

from rexecop.adapters.sclite_port.contracts import SCLITE_SCHEMA_REFS
from rexecop.errors import RExecOpValidationError
from rexecop.evidence.event import EvidenceEventType
from rexecop.evidence.manager import EvidenceManager
from rexecop.runtime_ops.coordinator import ACTIVE_RUNTIME_STATES
from rexecop.storage.atomic import atomic_write_text, secure_directory, secure_file
from rexecop.storage.port import RuntimeStore

WATCHDOG_SCHEMA = "rexecop.watchdog_record.v0.1"
DEFAULT_WORKER_ID = "local-worker"
DEFAULT_INBOX_RETRY_BUDGET = 3
DEFAULT_STALE_OPERATION_SECONDS = 3600.0


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _timestamp(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


class WatchdogService:
    """Domain-neutral supervisor for RExecOp's own runtime mechanics."""

    def __init__(self, store: RuntimeStore) -> None:
        self.store = store
        self.root = store.root
        self.watchdog_dir = self.root / "watchdog"
        self.records_dir = self.watchdog_dir / "records"
        self.projections_dir = self.watchdog_dir / "sclite_projection"
        self.dead_letter_dir = self.root / "dead_letter"
        self.retry_budget_file = self.watchdog_dir / "retry_budget.json"

    def ensure_layout(self) -> None:
        self.store.ensure_layout()
        secure_directory(self.watchdog_dir)
        secure_directory(self.records_dir)
        secure_directory(self.projections_dir)
        secure_directory(self.dead_letter_dir)

    def record_heartbeat(
        self,
        *,
        worker_id: str = DEFAULT_WORKER_ID,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not worker_id.strip():
            raise RExecOpValidationError("worker_id must not be empty")
        observed_at = now or _utc_now()
        record = self._write_record(
            observation="worker_heartbeat",
            decision="record_health",
            observed_at=observed_at,
            payload={
                "worker_id": worker_id,
                "monotonic_seconds": round(time.monotonic(), 6),
            },
        )
        atomic_write_text(
            self.watchdog_dir / "heartbeat.json",
            json.dumps(record, indent=2, sort_keys=True) + "\n",
        )
        return record

    def record_queue_depth(
        self,
        *,
        depth: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if depth < 0:
            raise RExecOpValidationError("queue depth must not be negative")
        return self._write_record(
            observation="queue_depth",
            decision="record_health",
            observed_at=now or _utc_now(),
            payload={"depth": depth},
        )

    def move_stale_inbox_items(
        self,
        *,
        max_age_seconds: float,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if max_age_seconds <= 0:
            raise RExecOpValidationError("max_age_seconds must be positive")
        self.ensure_layout()
        inbox = self.root / "inbox"
        if not inbox.is_dir():
            return []
        secure_directory(inbox)

        observed_at = now or _utc_now()
        now_seconds = observed_at.timestamp()
        records: list[dict[str, Any]] = []
        for path in sorted(inbox.glob("*.json")):
            age_seconds = max(0.0, now_seconds - path.stat().st_mtime)
            if age_seconds <= max_age_seconds:
                continue
            records.append(
                self.move_inbox_item_to_dead_letter(
                    path,
                    reason="stale_inbox_item",
                    observed_at=observed_at,
                    details={
                        "age_seconds": round(age_seconds, 3),
                        "max_age_seconds": max_age_seconds,
                    },
                )
            )
        return records

    def move_inbox_item_to_dead_letter(
        self,
        path: Path,
        *,
        reason: str,
        observed_at: datetime | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.ensure_layout()
        if not path.is_file():
            raise RExecOpValidationError(f"inbox item not found: {path.name}")
        secure_file(path)
        timestamp = _timestamp(observed_at or _utc_now())
        destination = self.dead_letter_dir / f"{timestamp}-{uuid.uuid4().hex[:8]}-{path.name}"
        path.replace(destination)
        secure_file(destination)
        record = self._write_record(
            observation="inbox_item",
            decision="move_to_dead_letter",
            observed_at=observed_at or _utc_now(),
            payload={
                "reason": reason,
                "source_name": path.name,
                "dead_letter_name": destination.name,
                "details": dict(details or {}),
            },
        )
        return record

    def record_inbox_processing_failure(
        self,
        path: Path,
        *,
        error_type: str,
        max_attempts: int = DEFAULT_INBOX_RETRY_BUDGET,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        if max_attempts <= 0:
            raise RExecOpValidationError("max_attempts must be positive")
        self.ensure_layout()
        state = self._load_retry_budget()
        item = dict(state.get(path.name) or {})
        attempts = int(item.get("attempts") or 0) + 1
        item = {
            "attempts": attempts,
            "max_attempts": max_attempts,
            "last_error_type": error_type,
            "updated_at": (observed_at or _utc_now()).isoformat(),
        }
        state[path.name] = item
        self._save_retry_budget(state)

        if attempts >= max_attempts:
            state.pop(path.name, None)
            self._save_retry_budget(state)
            return self.move_inbox_item_to_dead_letter(
                path,
                reason="retry_budget_exhausted",
                observed_at=observed_at,
                details={
                    "attempts": attempts,
                    "max_attempts": max_attempts,
                    "error_type": error_type,
                },
            )

        # Keep the item in the inbox for a later poll, but refresh mtime so
        # stale-item handling does not dead-letter it during the retry budget.
        now_seconds = (observed_at or _utc_now()).timestamp()
        path.touch()
        path.chmod(0o600)
        return self._write_record(
            observation="inbox_item",
            decision="retry_later",
            observed_at=observed_at or _utc_now(),
            payload={
                "reason": "inbox_processing_failed",
                "source_name": path.name,
                "details": {
                    "attempts": attempts,
                    "max_attempts": max_attempts,
                    "error_type": error_type,
                    "not_before_epoch_seconds": round(now_seconds, 3),
                },
            },
        )

    def clear_inbox_retry_budget(self, source_name: str) -> None:
        self.ensure_layout()
        state = self._load_retry_budget()
        if source_name in state:
            state.pop(source_name, None)
            self._save_retry_budget(state)

    def record_stale_active_operations(
        self,
        *,
        max_age_seconds: float = DEFAULT_STALE_OPERATION_SECONDS,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if max_age_seconds <= 0:
            raise RExecOpValidationError("max_age_seconds must be positive")
        observed_at = now or _utc_now()
        records: list[dict[str, Any]] = []
        for operation in self.store.list_operations():
            if operation.state not in ACTIVE_RUNTIME_STATES:
                continue
            updated_at = _parse_time(operation.updated_at)
            age_seconds = max(0.0, observed_at.timestamp() - updated_at.timestamp())
            if age_seconds <= max_age_seconds:
                continue
            records.append(
                self._write_record(
                    observation="stuck_operation",
                    decision="block_autostart",
                    observed_at=observed_at,
                    payload={
                        "reason": "stale_active_operation",
                        "operation_id": operation.id,
                        "state": operation.state,
                        "details": {
                            "age_seconds": round(age_seconds, 3),
                            "max_age_seconds": max_age_seconds,
                        },
                    },
                )
            )
        return records

    def _write_record(
        self,
        *,
        observation: str,
        decision: str,
        observed_at: datetime,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.ensure_layout()
        record_id = f"wd-{_timestamp(observed_at)}-{uuid.uuid4().hex[:12]}"
        record = {
            "schema": WATCHDOG_SCHEMA,
            "record_id": record_id,
            "observed_at": observed_at.isoformat(),
            "observation": observation,
            "decision": decision,
            "payload": payload,
        }
        atomic_write_text(
            self.records_dir / f"{record_id}.json",
            json.dumps(record, indent=2, sort_keys=True) + "\n",
        )
        self._write_sclite_projection(record)
        self._emit_operation_evidence(record)
        return record

    def _load_retry_budget(self) -> dict[str, Any]:
        if not self.retry_budget_file.is_file():
            return {}
        data = json.loads(self.retry_budget_file.read_text(encoding="utf-8"))
        return dict(data) if isinstance(data, dict) else {}

    def _save_retry_budget(self, data: dict[str, Any]) -> None:
        atomic_write_text(
            self.retry_budget_file,
            json.dumps(data, indent=2, sort_keys=True) + "\n",
        )

    def _write_sclite_projection(self, record: dict[str, Any]) -> None:
        projection = {
            "authority": "rexecop_runtime_projection",
            "future_artifact": "evidence_contract",
            "sclite_schema_ref": SCLITE_SCHEMA_REFS["evidence_contract"],
            "record_ref": {
                "schema": record["schema"],
                "record_id": record["record_id"],
                "observed_at": record["observed_at"],
                "observation": record["observation"],
                "decision": record["decision"],
            },
            "record_descriptor": artifact_descriptor(
                {
                    "schema_ref": SCLITE_SCHEMA_REFS["evidence_contract"],
                    "record": record,
                }
            ),
            "non_claims": [
                "projection_is_not_sclite_authority",
                "rexecop_watchdog_does_not_interpret_domain_health",
            ],
        }
        atomic_write_text(
            self.projections_dir / f"{record['record_id']}.json",
            json.dumps(projection, indent=2, sort_keys=True) + "\n",
        )

    def _emit_operation_evidence(self, record: dict[str, Any]) -> None:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return
        operation_id = payload.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            return
        EvidenceManager(self.store).emit(
            operation_id=operation_id,
            event_type=EvidenceEventType.WATCHDOG_DECISION,
            actor="rexecop.watchdog",
            payload={
                "record_id": record["record_id"],
                "observation": record["observation"],
                "decision": record["decision"],
                "reason": payload.get("reason"),
            },
        )


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
