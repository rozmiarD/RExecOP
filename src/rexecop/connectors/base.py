from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ConnectorRequest:
    connector: str
    action: str
    target: str
    mode: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectorResponse:
    connector: str
    action: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "connector": self.connector,
            "action": self.action,
            "success": self.success,
            "data": dict(self.data),
            "error": self.error,
        }


class ConnectorRuntime(Protocol):
    def invoke(self, request: ConnectorRequest) -> ConnectorResponse: ...
