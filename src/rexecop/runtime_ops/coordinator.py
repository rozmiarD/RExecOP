from __future__ import annotations

from typing import Any, Literal

from rexecop.adapters.govengine_port.contracts import is_mutating_mode
from rexecop.errors import RExecOpValidationError
from rexecop.operation.model import Operation
from rexecop.operation.state import OperationState
from rexecop.runtime_ops.maintenance import maintenance_window_allows
from rexecop.runtime_ops.queue import RunNowQueue
from rexecop.runtime_ops.target_lock import TargetLockManager
from rexecop.storage.port import RuntimeStore

AdmissionStatus = Literal["admitted", "queued"]

ACTIVE_RUNTIME_STATES = frozenset(
    {
        OperationState.RUNNING.value,
        OperationState.PAUSED.value,
        OperationState.RESUMING.value,
        OperationState.RETRYING.value,
        OperationState.VALIDATING.value,
    }
)


class RuntimeCoordinator:
    def __init__(self, store: RuntimeStore) -> None:
        self.store = store
        self.target_lock = TargetLockManager(store)
        self.queue = RunNowQueue(store)

    def runtime_policy(self, operation: Operation) -> dict[str, Any]:
        policy = operation.metadata.get("runtime_policy")
        return dict(policy) if isinstance(policy, dict) else {}

    def check_maintenance_window(self, operation: Operation) -> None:
        if not is_mutating_mode(operation.mode):
            return
        policy = self.runtime_policy(operation)
        windows = policy.get("maintenance_windows")
        if not isinstance(windows, list) or not windows:
            return
        allowed, reason = maintenance_window_allows(windows)
        if not allowed:
            raise RExecOpValidationError(
                f"apply blocked outside maintenance window ({reason})"
            )

    def count_active_operations(self, *, exclude_operation_id: str | None = None) -> int:
        count = 0
        for operation in self.store.list_operations():
            if exclude_operation_id and operation.id == exclude_operation_id:
                continue
            if operation.state in ACTIVE_RUNTIME_STATES:
                count += 1
        return count

    def admit_for_execution(self, operation: Operation) -> AdmissionStatus:
        if not is_mutating_mode(operation.mode):
            return "admitted"
        policy = self.runtime_policy(operation)
        max_concurrent = int(policy.get("max_concurrent_operations") or 1)
        target_lock_enabled = bool(policy.get("target_lock_enabled", True))

        if target_lock_enabled and not self.target_lock.try_acquire(
            environment=operation.environment,
            target=operation.target,
            operation_id=operation.id,
        ):
            self._mark_queued(operation, reason="target_locked")
            return "queued"

        if self.count_active_operations(exclude_operation_id=operation.id) >= max_concurrent:
            if target_lock_enabled:
                self.target_lock.release(
                    environment=operation.environment,
                    target=operation.target,
                    operation_id=operation.id,
                )
            self._mark_queued(operation, reason="max_concurrent_reached")
            return "queued"

        self.queue.remove(operation.id)
        operation.metadata.pop("queue", None)
        self.store.save_operation(operation)
        return "admitted"

    def release_operation(self, operation: Operation) -> None:
        if not is_mutating_mode(operation.mode):
            return
        policy = self.runtime_policy(operation)
        if bool(policy.get("target_lock_enabled", True)):
            self.target_lock.release(
                environment=operation.environment,
                target=operation.target,
                operation_id=operation.id,
            )
        self.queue.remove(operation.id)

    def _mark_queued(self, operation: Operation, *, reason: str) -> None:
        position = self.queue.enqueue(operation.id)
        operation.metadata["queue"] = {
            "status": "pending",
            "reason": reason,
            "position": position,
        }
        self.store.save_operation(operation)
