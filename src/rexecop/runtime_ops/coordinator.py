from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from rexecop.adapters.govengine_port.contracts import is_mutating_mode
from rexecop.errors import RExecOpConcurrencyConflict, RExecOpValidationError
from rexecop.operation.model import Operation
from rexecop.operation.state import OperationState
from rexecop.runtime_ops.maintenance import maintenance_window_allows
from rexecop.runtime_ops.queue import (
    QUEUE_CLAIM_RECOVERY_BLOCKED,
    RunNowQueue,
    StoreRunNowQueue,
)
from rexecop.runtime_ops.target_lock import TargetLockManager
from rexecop.storage.port import RuntimeStore

AdmissionStatus = Literal["admitted", "queued"]


@dataclass(frozen=True)
class _SnapshotAdmission:
    status: AdmissionStatus
    reason: str
    claim_snapshot: Mapping[str, Any] | None

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
        self.queue = StoreRunNowQueue(store)

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
            raise RExecOpValidationError(f"apply blocked outside maintenance window ({reason})")

    def count_active_operations(self, *, exclude_operation_id: str | None = None) -> int:
        count = 0
        for operation in self.store.list_operations():
            if exclude_operation_id and operation.id == exclude_operation_id:
                continue
            if operation.state in ACTIVE_RUNTIME_STATES:
                count += 1
        return count

    def admit_for_execution(self, operation: Operation) -> AdmissionStatus:
        if self._public_queue_has_compatible_pending():
            return "queued"
        status, reason = self._assess_for_execution(operation)
        if status == "queued":
            self._mark_queued(operation, reason=reason)
            return "queued"
        if not is_mutating_mode(operation.mode):
            return "admitted"
        if not self._acquire_prechecked_target(operation):
            self._mark_queued(operation, reason="target_locked")
            return "queued"
        self._persist_prechecked_admission(operation)
        return "admitted"

    def _assess_for_execution(
        self,
        operation: Operation,
    ) -> tuple[AdmissionStatus, str]:
        """Assess runtime admission without changing locks, queue, or metadata."""
        if not is_mutating_mode(operation.mode):
            return "admitted", ""
        policy = self.runtime_policy(operation)
        max_concurrent = int(policy.get("max_concurrent_operations") or 1)
        target_lock_enabled = bool(policy.get("target_lock_enabled", True))

        if target_lock_enabled:
            lock = self.target_lock.read(operation.environment, operation.target)
            if (
                lock is not None
                and not self.target_lock.is_stale(lock)
                and str(lock.get("operation_id") or "") != operation.id
            ):
                return "queued", "target_locked"

        if self.count_active_operations(exclude_operation_id=operation.id) >= max_concurrent:
            return "queued", "max_concurrent_reached"

        return "admitted", ""

    def _acquire_prechecked_target(self, operation: Operation) -> bool:
        """Acquire only the final target lock after side-effect-free assessment."""
        if not is_mutating_mode(operation.mode):
            return True
        policy = self.runtime_policy(operation)
        if not bool(policy.get("target_lock_enabled", True)):
            return True
        return self.target_lock.try_acquire(
            environment=operation.environment,
            target=operation.target,
            operation_id=operation.id,
        )

    def _persist_prechecked_admission(self, operation: Operation) -> None:
        """Persist admitted queue state after final target acquisition."""
        operation.metadata.pop("queue", None)
        self.store.save_operation(operation)

    def _admit_for_controller(
        self,
        operation: Operation,
        lease: Mapping[str, Any],
        *,
        selection: Mapping[str, Any] | None = None,
    ) -> _SnapshotAdmission:
        selected = selection
        if selected is None:
            selected = self.queue.claim_specific_from_lease(
                operation.id,
                dict(lease),
            )
        claim_snapshot = self._selection_claim(
            selected,
            required_purpose="execution",
        )
        status, reason = self._assess_for_execution(operation)
        if status == "queued":
            self._defer_or_enqueue(
                operation,
                lease=lease,
                claim_snapshot=claim_snapshot,
                reason=reason,
            )
            return _SnapshotAdmission("queued", reason, None)
        if not self._acquire_prechecked_target(operation):
            self._defer_or_enqueue(
                operation,
                lease=lease,
                claim_snapshot=claim_snapshot,
                reason="target_locked",
            )
            return _SnapshotAdmission("queued", "target_locked", None)
        self._persist_prechecked_admission(operation)
        return _SnapshotAdmission("admitted", "", claim_snapshot)

    def _claim_terminal_for_controller(
        self,
        operation: Operation,
        lease: Mapping[str, Any],
        *,
        selection: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        selected = selection
        if selected is None:
            selected = self.queue.claim_specific_from_lease(
                operation.id,
                dict(lease),
            )
        return self._selection_claim(
            selected,
            required_purpose="terminal_cleanup",
        )

    def _selection_from_existing_claim(
        self,
        operation_id: str,
        lease: Mapping[str, Any],
        claim_snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        lifecycle = self._require_queue_lifecycle()
        lease_guard = lifecycle._enter_lease_guard(
            lease,
            completion_operation_id=operation_id,
        )
        try:
            with lifecycle._locked():
                data = lifecycle._load_unlocked()
                lifecycle._validate_strict_mutation_topology(data)
                current = data["claims"].get(operation_id)
                if not lifecycle._exact_current_claim(
                    current,
                    operation_id=operation_id,
                    lease=lease,
                    claim_snapshot=claim_snapshot,
                ):
                    lifecycle._claim_conflict(operation_id)
                state, attempts, load_reason = lifecycle._load_recovery_facts(
                    operation_id
                )
                purpose = lifecycle._specific_claim_purpose(
                    state=state,
                    attempts=attempts,
                    load_reason=load_reason,
                )
                return {"purpose": purpose, "claim": dict(current)}
        finally:
            lease_guard.__exit__(*sys.exc_info())

    def _public_queue_has_compatible_pending(self) -> bool:
        lifecycle = self._require_queue_lifecycle()
        with lifecycle._locked():
            data = lifecycle._load_unlocked()
            lifecycle._validate_public_mutation(data)
            return bool(data["pending"])

    def _require_queue_lifecycle(self) -> RunNowQueue:
        return self.queue._lifecycle()

    @staticmethod
    def _selection_claim(
        selection: Mapping[str, Any] | None,
        *,
        required_purpose: str,
    ) -> Mapping[str, Any] | None:
        if selection is None:
            return None
        if set(selection) != {"purpose", "claim"} or (
            selection.get("purpose") != required_purpose
            or not isinstance(selection.get("claim"), Mapping)
        ):
            raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED) from None
        claim = selection.get("claim")
        if not isinstance(claim, Mapping):
            raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED) from None
        return claim

    def _defer_or_enqueue(
        self,
        operation: Operation,
        *,
        lease: Mapping[str, Any],
        claim_snapshot: Mapping[str, Any] | None,
        reason: str,
    ) -> None:
        if claim_snapshot is None:
            self._mark_queued(operation, reason=reason)
            return
        self.queue.defer_claim_from_lease(
            operation.id,
            claim_snapshot,
            dict(lease),
            reason=reason,
        )
        self._record_deferred_claim(operation, reason=reason)

    def _record_deferred_claim(self, operation: Operation, *, reason: str) -> None:
        """Project an already committed claim deferral into operation metadata."""
        position = self.queue.position(operation.id)
        if position is None:
            raise RExecOpConcurrencyConflict(
                "concurrency_conflict: deferred queue claim is no longer pending"
            )
        operation.metadata["queue"] = {
            "status": "pending",
            "reason": reason,
            "position": position,
        }
        self.store.save_operation(operation)

    def release_operation(self, operation: Operation) -> None:
        self.queue.discard_pending(operation.id)
        self._release_target_only(operation)

    def _release_target_only(self, operation: Operation) -> None:
        if not is_mutating_mode(operation.mode):
            return
        policy = self.runtime_policy(operation)
        if bool(policy.get("target_lock_enabled", True)):
            self.target_lock.release(
                environment=operation.environment,
                target=operation.target,
                operation_id=operation.id,
            )

    def _mark_queued(self, operation: Operation, *, reason: str) -> None:
        position = self.queue.enqueue(operation.id)
        operation.metadata["queue"] = {
            "status": "pending",
            "reason": reason,
            "position": position,
        }
        self.store.save_operation(operation)
