from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def parse_timeout_seconds(value: str) -> int:
    raw = (value or "").strip().lower()
    if not raw:
        return 0
    if raw.endswith("s"):
        return int(raw[:-1] or "0")
    if raw.endswith("m"):
        return int(raw[:-1] or "0") * 60
    return int(raw)


@dataclass
class StepMonitorStatus:
    operation_id: str
    current_step_id: str
    state: str
    timeout_seconds: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "current_step_id": self.current_step_id,
            "state": self.state,
            "timeout_seconds": self.timeout_seconds,
        }


class OperationMonitor:
    def status(
        self,
        *,
        operation_id: str,
        state: str,
        current_step_id: str,
        step: dict[str, Any] | None = None,
    ) -> StepMonitorStatus:
        timeout = parse_timeout_seconds(str((step or {}).get("timeout") or ""))
        return StepMonitorStatus(
            operation_id=operation_id,
            current_step_id=current_step_id,
            state=state,
            timeout_seconds=timeout,
        )
