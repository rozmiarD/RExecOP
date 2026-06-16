from __future__ import annotations

from typing import Any, Protocol

from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan


class OperationStoragePort(Protocol):
    """Storage port boundary; FileStore remains the default backend."""

    def save_operation(self, operation: Operation) -> None: ...

    def load_operation(self, operation_id: str) -> Operation: ...

    def list_operations(self) -> list[Operation]: ...

    def save_plan(self, plan: OperationPlan) -> None: ...

    def load_plan(self, operation_id: str) -> OperationPlan: ...

    def save_evidence_event(self, operation_id: str, event: dict[str, Any]) -> None: ...

    def list_evidence_events(self, operation_id: str) -> list[dict[str, Any]]: ...
