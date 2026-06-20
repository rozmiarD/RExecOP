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
    policy_pack: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "profile": self.profile,
            "description": self.description,
            "targets": dict(self.targets),
            "connectors": dict(self.connectors),
            "safety": dict(self.safety),
            "policy_pack": dict(self.policy_pack) if isinstance(self.policy_pack, dict) else None,
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Environment:
        raw = data.get("environment")
        if not isinstance(raw, dict):
            raise ValueError("environment mapping required")
        env_id = str(raw.get("id") or "").strip()
        if not env_id:
            raise ValueError("environment.id is required")
        policy_pack = raw.get("policy_pack")
        if policy_pack is not None and not isinstance(policy_pack, dict):
            raise ValueError("environment.policy_pack must be a mapping")
        return cls(
            id=env_id,
            profile=str(raw.get("profile") or ""),
            description=str(raw.get("description") or ""),
            targets=dict(raw.get("targets") or {}),
            connectors=dict(raw.get("connectors") or {}),
            safety=dict(raw.get("safety") or {}),
            policy_pack=dict(policy_pack) if isinstance(policy_pack, dict) else None,
        )
