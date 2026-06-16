from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class GovEngineDecisionType(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    APPROVAL_REQUIRED = "approval_required"
    MAINTENANCE_WINDOW_REQUIRED = "maintenance_window_required"
    BACKUP_REQUIRED = "backup_required"
    READ_ONLY_ONLY = "read_only_only"
    HUMAN_REQUIRED = "human_required"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


BLOCKING_DECISIONS = frozenset(
    {
        GovEngineDecisionType.BLOCKED,
        GovEngineDecisionType.READ_ONLY_ONLY,
        GovEngineDecisionType.HUMAN_REQUIRED,
        GovEngineDecisionType.UNSUPPORTED,
        GovEngineDecisionType.ERROR,
    }
)

WAITING_DECISIONS = frozenset(
    {
        GovEngineDecisionType.APPROVAL_REQUIRED,
        GovEngineDecisionType.MAINTENANCE_WINDOW_REQUIRED,
        GovEngineDecisionType.BACKUP_REQUIRED,
    }
)

MUTATING_MODES = frozenset({"apply", "recovery"})


def is_mutating_mode(mode: str) -> bool:
    return mode in MUTATING_MODES


@dataclass(frozen=True)
class GovEngineRequest:
    operation_id: str
    profile: str
    environment: str
    intent: str
    target: str
    mode: str
    risk: str
    preview: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "profile": self.profile,
            "environment": self.environment,
            "intent": self.intent,
            "target": self.target,
            "mode": self.mode,
            "risk": self.risk,
            "preview": dict(self.preview),
        }


@dataclass(frozen=True)
class GovEngineDecision:
    decision_type: GovEngineDecisionType
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_type": self.decision_type.value,
            "summary": self.summary,
            "details": dict(self.details),
        }

    @property
    def allows_mutating_execution(self) -> bool:
        return self.decision_type == GovEngineDecisionType.ALLOWED


class GovEngineAdapter(Protocol):
    def evaluate(self, request: GovEngineRequest) -> GovEngineDecision: ...
