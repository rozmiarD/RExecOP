from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class StepExecutionContext:
    operation_id: str
    target: str
    mode: str
    step: dict[str, Any]
    shared_state: dict[str, Any]


@dataclass
class StepExecutionResult:
    step_id: str
    success: bool
    output: dict[str, Any]
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "success": self.success,
            "output": dict(self.output),
            "error": self.error,
        }
