from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from rexecop.operation.state import OperationState


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class StateTransitionRecord:
    from_state: str
    to_state: str
    timestamp_utc: str
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "from_state": self.from_state,
            "to_state": self.to_state,
            "timestamp_utc": self.timestamp_utc,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateTransitionRecord:
        return cls(
            from_state=str(data["from_state"]),
            to_state=str(data["to_state"]),
            timestamp_utc=str(data["timestamp_utc"]),
            reason=str(data.get("reason") or ""),
        )


@dataclass
class Operation:
    id: str
    profile: str
    environment: str
    intent: str
    target: str
    mode: str
    requested_by: str
    state: str
    created_at: str
    updated_at: str
    correlation_id: str = ""
    current_step_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    history: list[StateTransitionRecord] = field(default_factory=list)
    evidence_event_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "profile": self.profile,
            "environment": self.environment,
            "intent": self.intent,
            "target": self.target,
            "mode": self.mode,
            "requested_by": self.requested_by,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "correlation_id": self.correlation_id,
            "current_step_id": self.current_step_id,
            "metadata": dict(self.metadata),
            "history": [item.as_dict() for item in self.history],
            "evidence_event_ids": list(self.evidence_event_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Operation:
        history = [
            StateTransitionRecord.from_dict(item)
            for item in data.get("history") or []
        ]
        return cls(
            id=str(data["id"]),
            profile=str(data["profile"]),
            environment=str(data["environment"]),
            intent=str(data["intent"]),
            target=str(data["target"]),
            mode=str(data["mode"]),
            requested_by=str(data.get("requested_by") or "operator"),
            state=str(data["state"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            correlation_id=str(data.get("correlation_id") or ""),
            current_step_id=str(data.get("current_step_id") or ""),
            metadata=dict(data.get("metadata") or {}),
            history=history,
            evidence_event_ids=[str(item) for item in data.get("evidence_event_ids") or []],
        )

    @property
    def operation_state(self) -> OperationState:
        return OperationState(self.state)
