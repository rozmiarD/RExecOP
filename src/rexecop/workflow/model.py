from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowStep:
    id: str
    type: str
    action: str
    connector: str = ""
    timeout: str = ""
    pause_safe: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "action": self.action,
            "pause_safe": self.pause_safe,
        }
        if self.connector:
            data["connector"] = self.connector
        if self.timeout:
            data["timeout"] = self.timeout
        if self.metadata:
            data["metadata"] = dict(self.metadata)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStep:
        return cls(
            id=str(data["id"]),
            type=str(data.get("type") or "internal"),
            action=str(data.get("action") or ""),
            connector=str(data.get("connector") or ""),
            timeout=str(data.get("timeout") or ""),
            pause_safe=bool(data.get("pause_safe", False)),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class Workflow:
    id: str
    intent: str
    mode: str
    risk: str
    description: str
    steps: list[WorkflowStep]
    retry: dict[str, Any] = field(default_factory=dict)
    rollback: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "intent": self.intent,
            "mode": self.mode,
            "risk": self.risk,
            "description": self.description,
            "steps": [step.as_dict() for step in self.steps],
            "retry": dict(self.retry),
            "rollback": dict(self.rollback),
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Workflow:
        raw = data.get("workflow") if "workflow" in data else data
        if not isinstance(raw, dict):
            raise ValueError("workflow mapping required")
        steps = [WorkflowStep.from_dict(item) for item in raw.get("steps") or []]
        return cls(
            id=str(raw.get("id") or ""),
            intent=str(raw.get("intent") or ""),
            mode=str(raw.get("mode") or "read_only"),
            risk=str(raw.get("risk") or "unknown"),
            description=str(raw.get("description") or ""),
            steps=steps,
            retry=dict(raw.get("retry") or {}),
            rollback=dict(raw.get("rollback") or {}),
        )

    def required_connectors(self) -> list[str]:
        connectors: list[str] = []
        for step in self.steps:
            if step.type == "connector" and step.connector and step.connector not in connectors:
                connectors.append(step.connector)
        return connectors

    def pause_safe_points(self) -> list[str]:
        return [step.id for step in self.steps if step.pause_safe]
