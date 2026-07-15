from __future__ import annotations

import json
from typing import Any

from rexecop.errors import RExecOpConcurrencyConflict, RExecOpValidationError
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.storage.file_store import FileStore


class InMemoryStore:
    """In-memory OperationStoragePort for tests and ephemeral runs."""

    def __init__(self) -> None:
        self._operations: dict[str, Operation] = {}
        self._plans: dict[str, OperationPlan] = {}
        self._evidence: dict[str, list[dict[str, Any]]] = {}
        self._structured_logs: list[dict[str, Any]] = []
        self._file_store = FileStore()

    def ensure_layout(self) -> None:
        return None

    @property
    def root(self) -> Any:
        return self._file_store.root

    def save_operation(self, operation: Operation) -> None:
        current = self._operations.get(operation.id)
        current_revision = current.operation_revision if current is not None else 0
        if current_revision != operation.operation_revision:
            raise RExecOpConcurrencyConflict(
                f"concurrency_conflict: operation {operation.id} expected revision "
                f"{operation.operation_revision}, found {current_revision}"
            )
        operation.operation_revision = current_revision + 1
        self._operations[operation.id] = Operation.from_dict(operation.as_dict())

    def load_operation(self, operation_id: str) -> Operation:
        operation = self._operations.get(operation_id)
        if operation is None:
            raise RExecOpValidationError(f"operation not found: {operation_id}")
        return Operation.from_dict(operation.as_dict())

    def list_operations(self) -> list[Operation]:
        return [Operation.from_dict(item.as_dict()) for item in self._operations.values()]

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

    def save_structured_log_event(self, event: dict[str, Any]) -> None:
        self._structured_logs.append(dict(event))

    def list_structured_log_events(
        self,
        *,
        operation_id: str | None = None,
        correlation_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 200))
        items = list(self._structured_logs)
        if operation_id:
            items = [
                item
                for item in items
                if str((item.get("refs") or {}).get("operation_id") or "") == operation_id
            ]
        if correlation_id:
            items = [
                item for item in items if str(item.get("correlation_id") or "") == correlation_id
            ]
        return items[-bounded_limit:]

    def operation_sclite_dir(self, operation_id: str) -> Any:
        return self._file_store.operation_sclite_dir(operation_id)

    def save_receipt_export(self, operation_id: str, export: dict[str, Any]) -> Any:
        return self._file_store.save_receipt_export(operation_id, export)

    def load_receipt_export(self, operation_id: str) -> dict[str, Any]:
        return self._file_store.load_receipt_export(operation_id)

    def save_approval(self, operation_id: str, approval: dict[str, Any]) -> Any:
        return self._file_store.save_approval(operation_id, approval)

    def load_approval(self, operation_id: str) -> dict[str, Any]:
        return self._file_store.load_approval(operation_id)

    def acquire_execution_lease(self, *, worker_id: str) -> dict[str, Any]:
        return self._file_store.acquire_execution_lease(worker_id=worker_id)

    def renew_execution_lease(self, lease: dict[str, Any]) -> dict[str, Any]:
        return self._file_store.renew_execution_lease(lease)

    def release_execution_lease(self, lease: dict[str, Any]) -> bool:
        return self._file_store.release_execution_lease(lease)

    def validate_execution_lease(self, lease: dict[str, Any]) -> None:
        self._file_store.validate_execution_lease(lease)

    def queue_list_pending(self) -> list[str]:
        return self._file_store.queue_list_pending()

    def queue_position(self, operation_id: str) -> int | None:
        return self._file_store.queue_position(operation_id)

    def queue_enqueue(self, operation_id: str) -> int:
        return self._file_store.queue_enqueue(operation_id)

    def queue_remove(self, operation_id: str) -> None:
        self._file_store.queue_remove(operation_id)

    def queue_discard_pending(self, operation_id: str) -> None:
        self._file_store.queue_discard_pending(operation_id)

    def queue_claim(self, lease: dict[str, Any]) -> dict[str, Any] | None:
        return self._file_store.queue_claim(lease)

    def queue_complete_claim(self, operation_id: str, lease: dict[str, Any]) -> None:
        self._file_store.queue_complete_claim(operation_id, lease)

    def start_execution_attempt(self, **binding: Any) -> dict[str, Any]:
        return self._file_store.start_execution_attempt(**binding)

    def allocate_execution_attempt_id(self) -> str:
        return self._file_store.allocate_execution_attempt_id()

    def finish_execution_attempt(
        self,
        attempt: dict[str, Any],
        *,
        status: str,
        result_digest: str = "",
        error_class: str = "",
    ) -> dict[str, Any]:
        return self._file_store.finish_execution_attempt(
            attempt,
            status=status,
            result_digest=result_digest,
            error_class=error_class,
        )

    def recover_started_attempts(self) -> list[str]:
        return self._file_store.recover_started_attempts()

    def has_indeterminate_side_effect(self, operation_id: str) -> bool:
        return self._file_store.has_indeterminate_side_effect(operation_id)

    def list_pending_projection_operations(self) -> list[Operation]:
        return [
            operation
            for operation in self.list_operations()
            if isinstance(operation.metadata.get("sclite_projection"), dict)
            and operation.metadata["sclite_projection"].get("status") == "pending"
        ]

    def save_execution_permit(self, permit: dict[str, Any]) -> Any:
        return self._file_store.save_execution_permit(permit)

    def load_execution_permit(self, operation_id: str, step_id: str) -> dict[str, Any]:
        return self._file_store.load_execution_permit(operation_id, step_id)

    def load_execution_permit_for_attempt(
        self,
        operation_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        return self._file_store.load_execution_permit_for_attempt(operation_id, attempt_id)

    def claim_governance_decision_once(self, **claim: Any) -> bool:
        return self._file_store.claim_governance_decision_once(**claim)

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
