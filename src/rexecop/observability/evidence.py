from __future__ import annotations

from typing import Any

from rexecop.evidence.event import EvidenceEventType
from rexecop.evidence.manager import EvidenceManager
from rexecop.observability.emitter import StructuredLogEmitter
from rexecop.observability.structured_log import StructuredLogRefs


class ObservabilityEvidenceManager(EvidenceManager):
    def __init__(
        self,
        store: Any,
        structured_log: StructuredLogEmitter,
    ) -> None:
        super().__init__(store)
        self.structured_log = structured_log

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
        public_projection_allowlist: frozenset[str] | None = None,
    ) -> str:
        event_id = super().emit(
            operation_id=operation_id,
            event_type=event_type,
            actor=actor,
            state_before=state_before,
            state_after=state_after,
            step_id=step_id,
            correlation_id=correlation_id,
            payload=payload,
            public_projection_allowlist=public_projection_allowlist,
        )
        event_name = (
            event_type.value
            if isinstance(event_type, EvidenceEventType)
            else str(event_type)
        )
        self.structured_log.emit(
            event_kind="evidence_recorded",
            correlation_id=correlation_id,
            message=f"Evidence event recorded: {event_name}",
            refs=StructuredLogRefs(
                operation_id=operation_id,
                evidence_ref=event_id,
            ),
            details={
                "event_type": event_name,
                "step_id": step_id,
                "state_before": state_before,
                "state_after": state_after,
            },
        )
        return event_id