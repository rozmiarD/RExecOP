from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Environment:
    id: str
    profile: str
    description: str
    targets: dict[str, Any]
    connectors: dict[str, Any]
    safety: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "profile": self.profile,
            "description": self.description,
            "targets": dict(self.targets),
            "connectors": dict(self.connectors),
            "safety": dict(self.safety),
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Environment:
        raw = data.get("environment")
        if not isinstance(raw, dict):
            raise ValueError("environment mapping required")
        env_id = str(raw.get("id") or "").strip()
        if not env_id:
            raise ValueError("environment.id is required")
        return cls(
            id=env_id,
            profile=str(raw.get("profile") or ""),
            description=str(raw.get("description") or ""),
            targets=dict(raw.get("targets") or {}),
            connectors=dict(raw.get("connectors") or {}),
            safety=dict(raw.get("safety") or {}),
        )
