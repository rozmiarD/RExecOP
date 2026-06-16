from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class OperationPlan:
    operation_id: str
    profile: str
    environment: str
    intent: str
    target: str
    mode: str
    workflow: dict[str, Any]
    planned_steps: list[dict[str, Any]]
    required_connectors: list[str]
    risk: str
    govengine_request_preview: dict[str, Any]
    expected_evidence: list[str]
    pause_safe_points: list[str]
    retry_policy_summary: dict[str, Any]
    rollback_available: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "profile": self.profile,
            "environment": self.environment,
            "intent": self.intent,
            "target": self.target,
            "mode": self.mode,
            "workflow": dict(self.workflow),
            "planned_steps": [dict(step) for step in self.planned_steps],
            "required_connectors": list(self.required_connectors),
            "risk": self.risk,
            "govengine_request_preview": dict(self.govengine_request_preview),
            "expected_evidence": list(self.expected_evidence),
            "pause_safe_points": list(self.pause_safe_points),
            "retry_policy_summary": dict(self.retry_policy_summary),
            "rollback_available": self.rollback_available,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperationPlan:
        return cls(
            operation_id=str(data["operation_id"]),
            profile=str(data["profile"]),
            environment=str(data["environment"]),
            intent=str(data["intent"]),
            target=str(data["target"]),
            mode=str(data["mode"]),
            workflow=dict(data.get("workflow") or {}),
            planned_steps=[dict(step) for step in data.get("planned_steps") or []],
            required_connectors=[str(item) for item in data.get("required_connectors") or []],
            risk=str(data.get("risk") or "unknown"),
            govengine_request_preview=dict(data.get("govengine_request_preview") or {}),
            expected_evidence=[str(item) for item in data.get("expected_evidence") or []],
            pause_safe_points=[str(item) for item in data.get("pause_safe_points") or []],
            retry_policy_summary=dict(data.get("retry_policy_summary") or {}),
            rollback_available=bool(data.get("rollback_available", False)),
        )
