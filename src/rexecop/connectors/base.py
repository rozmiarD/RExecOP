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


def effective_timeout_seconds(request: ConnectorRequest, configured: float) -> float:
    policy = _execution_control(request, "timeout_seconds")
    if policy <= 0:
        return configured
    return min(configured, policy)


def effective_output_bytes(request: ConnectorRequest, configured: int) -> int:
    raw_policy = _execution_control(request, "max_output_bytes")
    if not raw_policy.is_integer():
        raise ValueError("invalid execution control: max_output_bytes")
    policy = int(raw_policy)
    if policy <= 0:
        return configured
    return min(configured, policy)


def _execution_control(request: ConnectorRequest, name: str) -> float:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    controls = metadata.get("execution_controls")
    if not isinstance(controls, dict):
        return 0.0
    try:
        value = float(controls.get(name) or 0.0)
    except (TypeError, ValueError):
        raise ValueError(f"invalid execution control: {name}") from None
    if value < 0:
        raise ValueError(f"invalid execution control: {name}")
    return value
