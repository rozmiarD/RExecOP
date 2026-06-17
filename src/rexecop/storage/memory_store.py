from __future__ import annotations

import json
from typing import Any

from rexecop.errors import RExecOpValidationError
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.storage.file_store import FileStore


class InMemoryStore:
    """In-memory OperationStoragePort for tests and ephemeral runs."""

    def __init__(self) -> None:
        self._operations: dict[str, Operation] = {}
        self._plans: dict[str, OperationPlan] = {}
        self._evidence: dict[str, list[dict[str, Any]]] = {}
        self._file_store = FileStore()

    def ensure_layout(self) -> None:
        return None

    @property
    def root(self) -> Any:
        return None

    def save_operation(self, operation: Operation) -> None:
        self._operations[operation.id] = operation

    def load_operation(self, operation_id: str) -> Operation:
        operation = self._operations.get(operation_id)
        if operation is None:
            raise RExecOpValidationError(f"operation not found: {operation_id}")
        return operation

    def list_operations(self) -> list[Operation]:
        return list(self._operations.values())

    def save_plan(self, plan: OperationPlan) -> None:
        self._plans[plan.operation_id] = plan

    def load_plan(self, operation_id: str) -> OperationPlan:
        plan = self._plans.get(operation_id)
        if plan is None:
            raise RExecOpValidationError(f"plan not found: {operation_id}")
        return plan

    def save_evidence_event(self, operation_id: str, event: dict[str, Any]) -> None:
        self._evidence.setdefault(operation_id, []).append(dict(event))

    def list_evidence_events(self, operation_id: str) -> list[dict[str, Any]]:
        return [dict(event) for event in self._evidence.get(operation_id, [])]

    def operation_sclite_dir(self, operation_id: str) -> Any:
        return self._file_store.operation_sclite_dir(operation_id)

    def save_receipt_export(self, operation_id: str, export: dict[str, Any]) -> Any:
        return self._file_store.save_receipt_export(operation_id, export)

    def load_receipt_export(self, operation_id: str) -> dict[str, Any]:
        return self._file_store.load_receipt_export(operation_id)

    def dump_state(self) -> dict[str, Any]:
        return {
            "operations": {
                operation_id: json.loads(json.dumps(operation.as_dict()))
                for operation_id, operation in self._operations.items()
            },
            "plans": {
                operation_id: json.loads(json.dumps(plan.as_dict()))
                for operation_id, plan in self._plans.items()
            },
            "evidence": self._evidence,
        }
