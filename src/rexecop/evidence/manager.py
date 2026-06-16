from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from rexecop.evidence.event import EvidenceEventType
from rexecop.evidence.redaction import redact_payload
from rexecop.storage.file_store import FileStore


class EvidenceManager:
    def __init__(self, store: FileStore) -> None:
        self.store = store

    def emit(
        self,
        *,
        operation_id: str,
        event_type: EvidenceEventType | str,
        actor: str = "operator",
        state_before: str = "",
        state_after: str = "",
        step_id: str = "",
        correlation_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> str:
        event_id = f"ev-{uuid.uuid4().hex[:12]}"
        sanitized = redact_payload(payload or {})
        event = {
            "event_id": event_id,
            "operation_id": operation_id,
            "event_type": (
                event_type.value
                if isinstance(event_type, EvidenceEventType)
                else str(event_type)
            ),
            "timestamp_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "actor": actor,
            "state_before": state_before,
            "state_after": state_after,
            "step_id": step_id,
            "correlation_id": correlation_id,
            "sanitized_payload": sanitized,
        }
        self.store.save_evidence_event(operation_id, event)
        return event_id
