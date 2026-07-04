from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from govengine import (
    SupervisorActionRequest,
    admit_supervisor_action,
    supervisor_action_admission_digest,
    supervisor_action_request_digest,
)
from sclite import build_watchdog_decision
from sclite.integrity import artifact_descriptor

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
MANUAL_RECOVERY_ACTIONS = {"renew_lease", "mark_stale", "escalate_operator"}


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
        self.sclite_dir = self.watchdog_dir / "sclite"
        self.dead_letter_dir = self.root / "dead_letter"
        self.retry_budget_file = self.watchdog_dir / "retry_budget.json"

    def ensure_layout(self) -> None:
        self.store.ensure_layout()
        secure_directory(self.watchdog_dir)
        secure_directory(self.records_dir)
        secure_directory(self.sclite_dir)
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
        event_time = observed_at or _utc_now()
        timestamp = _timestamp(event_time)
        destination = self.dead_letter_dir / f"{timestamp}-{uuid.uuid4().hex[:8]}-{path.name}"
        record = self._build_record(
            observation="inbox_item",
            decision="move_to_dead_letter",
            observed_at=event_time,
            payload={
                "reason": reason,
                "source_name": path.name,
                "dead_letter_name": destination.name,
                "details": dict(details or {}),
            },
        )
        admission_context = self._admit_record(record)
        path.replace(destination)
        secure_file(destination)
        self._persist_record(record, admission_context=admission_context)
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

    def record_manual_recovery_action(
        self,
        *,
        action: str,
        reason: str,
        actor_ref: str,
        scope: str,
        operation_id: str = "",
        event_ref: str = "",
        trigger_ref: str = "",
        inbox_item_name: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        action = action.strip()
        if action not in MANUAL_RECOVERY_ACTIONS:
            raise RExecOpValidationError(
                "manual watchdog action must be one of: "
                + ", ".join(sorted(MANUAL_RECOVERY_ACTIONS))
            )
        if not reason.strip():
            raise RExecOpValidationError("manual watchdog reason must not be empty")
        if not actor_ref.strip():
            raise RExecOpValidationError("manual watchdog actor_ref must not be empty")
        if not scope.strip():
            raise RExecOpValidationError("manual watchdog scope must not be empty")
        affected_refs = (
            operation_id.strip(),
            event_ref.strip(),
            trigger_ref.strip(),
            inbox_item_name.strip(),
        )
        if not any(affected_refs):
            raise RExecOpValidationError("manual watchdog action requires an affected reference")

        return self._write_record(
            observation="manual_recovery",
            decision=action,
            observed_at=now or _utc_now(),
            payload={
                "reason": reason,
                "actor_ref": actor_ref,
                "scope": scope,
                "human_signoff": True,
                "operation_id": operation_id.strip(),
                "event_ref": event_ref.strip(),
                "trigger_ref": trigger_ref.strip(),
                "source_name": inbox_item_name.strip(),
            },
        )

    def _write_record(
        self,
        *,
        observation: str,
        decision: str,
        observed_at: datetime,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.ensure_layout()
        record = self._build_record(
            observation=observation,
            decision=decision,
            observed_at=observed_at,
            payload=payload,
        )
        self._persist_record(record, admission_context=self._admit_record(record))
        return record

    def _build_record(
        self,
        *,
        observation: str,
        decision: str,
        observed_at: datetime,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        record_id = f"wd-{_timestamp(observed_at)}-{uuid.uuid4().hex[:12]}"
        return {
            "schema": WATCHDOG_SCHEMA,
            "record_id": record_id,
            "observed_at": observed_at.isoformat(),
            "observation": observation,
            "decision": decision,
            "payload": payload,
        }

    def _persist_record(
        self,
        record: dict[str, Any],
        *,
        admission_context: dict[str, Any],
    ) -> None:
        atomic_write_text(
            self.records_dir / f"{record['record_id']}.json",
            json.dumps(record, indent=2, sort_keys=True) + "\n",
        )
        self._write_sclite_watchdog_decision(record, admission_context=admission_context)
        self._emit_operation_evidence(record)

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

    def _admit_record(self, record: dict[str, Any]) -> dict[str, Any]:
        request = supervisor_request_from_record(record)
        admission = admit_supervisor_action(request)
        if not admission.allowed and record["decision"] in {
            "move_to_dead_letter",
            "retry_later",
            "block_autostart",
        }:
            raise RExecOpValidationError(
                f"watchdog action denied by GovEngine: {admission.reason_code}"
            )
        return {
            "request": request.as_dict(),
            "request_digest": supervisor_action_request_digest(request),
            "admission": admission.as_dict(),
            "admission_digest": supervisor_action_admission_digest(admission),
        }

    def _write_sclite_watchdog_decision(
        self,
        record: dict[str, Any],
        *,
        admission_context: dict[str, Any],
    ) -> None:
        payload = _payload(record)
        artifact = build_watchdog_decision(
            decision_id=str(record["record_id"]),
            decision=str(record["decision"]),
            reason=str(payload.get("reason") or record["decision"]),
            decided_at=str(record["observed_at"]),
            source="rexecop.watchdog",
            observation={
                "record_id": record["record_id"],
                "schema": record["schema"],
                "observation": record["observation"],
                "observed_at": record["observed_at"],
                "digest": _record_digest_ref(record),
            },
            admission=admission_context,
            affected={
                "operation_id": payload.get("operation_id"),
                "event_id": payload.get("event_id") or payload.get("event_ref"),
                "trigger_id": payload.get("trigger_id") or payload.get("trigger_ref"),
                "inbox_item_name": payload.get("source_name"),
            },
            domain_authority="runtime-neutral",
            manual_recovery=_manual_recovery_ref(payload),
        )
        atomic_write_text(
            self.sclite_dir / f"{record['record_id']}.json",
            json.dumps(artifact, indent=2, sort_keys=True) + "\n",
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


def supervisor_request_from_record(record: dict[str, Any]) -> SupervisorActionRequest:
    record_ref = _record_digest_ref(record)
    payload = _payload(record)
    details = _details(payload)
    return SupervisorActionRequest(
        request_id=str(record["record_id"]),
        action=str(record["decision"]),
        reason=str(payload.get("reason") or record["decision"]),
        watchdog_record_ref=record_ref,
        observation=str(record["observation"]),
        affected_kind=_affected_kind(record),
        operation_id=str(payload.get("operation_id") or ""),
        event_ref=_sha256_ref_or_empty(payload.get("event_ref")),
        trigger_ref=_sha256_ref_or_empty(payload.get("trigger_ref")),
        inbox_item_name=str(payload.get("source_name") or ""),
        actor_ref=str(payload.get("actor_ref") or ""),
        scope=str(payload.get("scope") or ""),
        attempt_count=_int(details.get("attempts")),
        max_attempts=_int(details.get("max_attempts")),
        age_seconds=_float(details.get("age_seconds")),
        max_age_seconds=_float(details.get("max_age_seconds")),
        human_signoff=bool(payload.get("human_signoff", False)),
    )


def _record_digest_ref(record: dict[str, Any]) -> str:
    return f"sha256:{artifact_descriptor(record)['digest']}"


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def _details(payload: dict[str, Any]) -> dict[str, Any]:
    details = payload.get("details")
    return dict(details) if isinstance(details, dict) else {}


def _affected_kind(record: dict[str, Any]) -> str:
    payload = _payload(record)
    if payload.get("operation_id"):
        return "operation"
    if payload.get("source_name"):
        return "inbox_item"
    if payload.get("worker_id"):
        return "worker"
    if "depth" in payload:
        return "queue"
    return str(record.get("observation") or "")


def _sha256_ref_or_empty(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("sha256:"):
        return text
    return f"sha256:{text}"


def _int(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(value)


def _float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def _manual_recovery_ref(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload.get("human_signoff"):
        return None
    return {
        "actor_ref": str(payload.get("actor_ref") or ""),
        "scope": str(payload.get("scope") or ""),
        "human_signoff": bool(payload.get("human_signoff", False)),
        "reason": str(payload.get("reason") or ""),
    }
