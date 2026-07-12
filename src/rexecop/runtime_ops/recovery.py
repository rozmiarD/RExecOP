from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rexecop.errors import RExecOpValidationError
from rexecop.evidence.event import EvidenceEventType
from rexecop.operation.model import Operation, StateTransitionRecord, utc_now_iso
from rexecop.operation.state import OperationState, validate_transition
from rexecop.runtime_ops.attempts import AttemptJournal
from rexecop.runtime_ops.coordinator import ACTIVE_RUNTIME_STATES
from rexecop.runtime_ops.lease import DEFAULT_LEASE_TTL_SECONDS, WorkerLeaseManager
from rexecop.runtime_ops.target_lock import TargetLockManager
from rexecop.storage.port import RuntimeStore

if TYPE_CHECKING:
    from rexecop.operation.controller import OperationController

RECOVERY_REPORT_SCHEMA = "rexecop.runtime_recovery.v0.1"
TERMINAL_RECEIPT_STATES = frozenset(
    {
        OperationState.COMPLETED.value,
        OperationState.FAILED.value,
        OperationState.ESCALATED.value,
    }
)
INTERRUPTIBLE_STATES = frozenset(ACTIVE_RUNTIME_STATES)


def run_startup_recovery(
    store: RuntimeStore,
    *,
    controller: OperationController | None = None,
    now: datetime | None = None,
    lease_ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    repair_receipts: bool = True,
    lease_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconcile runtime store after restart without re-running backend IO."""
    from rexecop.operation.controller import OperationController as _OperationController

    observed_at = now or datetime.now(UTC).replace(microsecond=0)
    ctrl = controller or _OperationController(store=store)
    lease = WorkerLeaseManager(store.root)
    locks = TargetLockManager(store)

    owned_lease = lease_record
    if owned_lease is None:
        owned_lease = lease.acquire(worker_id="runtime-recovery", now=observed_at)
    cleared_lease = False
    released_locks = _release_stale_locks(locks)
    interrupted = _interrupt_active_operations(ctrl, observed_at=observed_at)
    indeterminate_attempts = AttemptJournal(store.root).mark_started_indeterminate()
    receipt_repairs: list[dict[str, Any]] = []
    receipt_blockers: list[dict[str, Any]] = []
    if repair_receipts:
        receipt_repairs, receipt_blockers = _repair_terminal_receipts(ctrl)

    report = {
        "schema": RECOVERY_REPORT_SCHEMA,
        "runtime_root": str(store.root),
        "observed_at": observed_at.isoformat(),
        "actions": {
            "cleared_stale_worker_lease": cleared_lease,
            "released_stale_locks": released_locks,
            "interrupted_operations": interrupted,
            "indeterminate_attempts": indeterminate_attempts,
            "receipt_repairs": receipt_repairs,
            "receipt_blockers": receipt_blockers,
        },
        "summary": {
            "changed": bool(
                cleared_lease
                or released_locks
                or interrupted
                or indeterminate_attempts
                or receipt_repairs
                or receipt_blockers
            ),
            "interrupted_count": len(interrupted),
            "indeterminate_attempt_count": len(indeterminate_attempts),
            "released_lock_count": len(released_locks),
            "receipt_repair_count": len(receipt_repairs),
            "receipt_blocker_count": len(receipt_blockers),
        },
    }
    if lease_record is None:
        lease.release(
            owner_token=str(owned_lease["owner_token"]),
            lease_epoch=int(owned_lease["lease_epoch"]),
            process_instance_id=str(owned_lease["process_instance_id"]),
        )
    return report


def operation_needs_receipt(operation: Operation, store: RuntimeStore) -> bool:
    if operation.state not in TERMINAL_RECEIPT_STATES:
        return False
    receipt_path = store.root / "receipts" / f"{operation.id}.json"
    if receipt_path.is_file():
        return False
    if isinstance(operation.metadata.get("recovery_blocker"), dict):
        return False
    return True


def ensure_terminal_receipt(
    controller: OperationController,
    operation_id: str,
) -> dict[str, Any]:
    operation = controller.get_operation(operation_id)
    if not operation_needs_receipt(operation, controller.store):
        return {"operation_id": operation_id, "status": "already_satisfied"}
    try:
        controller.store.load_plan(operation_id)
    except RExecOpValidationError as exc:
        return _record_receipt_blocker(
            controller,
            operation,
            reason="plan_missing",
            details={"error": exc.__class__.__name__},
        )
    try:
        export = controller.export_receipt(operation_id)
    except RExecOpValidationError as exc:
        return _record_receipt_blocker(
            controller,
            operation,
            reason="receipt_export_failed",
            details={"error": str(exc)},
        )
    return {
        "operation_id": operation_id,
        "status": "receipt_exported",
        "path": str(export.get("path") or ""),
    }


def start_is_idempotent(operation: Operation) -> bool:
    """Terminal operations must not re-run backend IO on repeated start."""
    return operation.state in TERMINAL_RECEIPT_STATES


def _release_stale_locks(lock_manager: TargetLockManager) -> list[dict[str, str]]:
    released: list[dict[str, str]] = []
    locks_dir = lock_manager.locks_dir
    if not locks_dir.is_dir():
        return released
    for path in sorted(locks_dir.glob("*.lock")):
        payload = _read_lock_file(path)
        if not payload:
            continue
        environment = str(payload.get("environment") or "")
        target = str(payload.get("target") or "")
        record = lock_manager.read(environment, target)
        if record is None or not lock_manager.is_stale(record):
            continue
        operation_id = str(record.get("operation_id") or "")
        lock_manager.release(environment=environment, target=target, operation_id=operation_id)
        released.append(
            {
                "environment": environment,
                "target": target,
                "operation_id": operation_id,
                "lock_file": path.name,
            }
        )
    return released


def _read_lock_file(path: Any) -> dict[str, Any] | None:
    import json
    from pathlib import Path

    if not isinstance(path, Path) or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _interrupt_active_operations(
    controller: OperationController,
    *,
    observed_at: datetime,
) -> list[dict[str, str]]:
    interrupted: list[dict[str, str]] = []
    for operation in controller.store.list_operations():
        if operation.state not in INTERRUPTIBLE_STATES:
            continue
        previous_state = operation.state
        _mark_interrupted(controller, operation, observed_at=observed_at)
        current = controller.get_operation(operation.id)
        interrupted.append(
            {
                "operation_id": operation.id,
                "previous_state": previous_state,
                "current_state": current.state,
            }
        )
    return interrupted


def _mark_interrupted(
    controller: OperationController,
    operation: Operation,
    *,
    observed_at: datetime,
) -> None:
    state = OperationState(operation.state)
    if state == OperationState.RUNNING:
        _transition_operation(
            controller,
            operation,
            OperationState.FAILED,
            reason="interrupted_by_restart",
        )
    elif state == OperationState.VALIDATING:
        _transition_operation(
            controller,
            operation,
            OperationState.FAILED,
            reason="interrupted_by_restart",
        )
    elif state in {OperationState.PAUSED, OperationState.RESUMING, OperationState.RETRYING}:
        if state == OperationState.PAUSED:
            _transition_operation(
                controller,
                operation,
                OperationState.RESUMING,
                reason="interrupted_resume",
            )
            operation = controller.get_operation(operation.id)
        if operation.state == OperationState.RESUMING.value:
            _transition_operation(
                controller,
                operation,
                OperationState.RUNNING,
                reason="interrupted_resume",
            )
            operation = controller.get_operation(operation.id)
        if operation.state == OperationState.RETRYING.value:
            _transition_operation(
                controller,
                operation,
                OperationState.RUNNING,
                reason="interrupted_retry",
            )
            operation = controller.get_operation(operation.id)
        if operation.state == OperationState.RUNNING.value:
            _transition_operation(
                controller,
                operation,
                OperationState.FAILED,
                reason="interrupted_by_restart",
            )
    operation = controller.get_operation(operation.id)
    controller.runtime.release_operation(operation)
    operation = controller.get_operation(operation.id)
    recovery = dict(operation.metadata.get("recovery") or {})
    recovery.update(
        {
            "interrupted_at": observed_at.isoformat(),
            "reason": "interrupted_by_restart",
            "resume_allowed": False,
        }
    )
    operation.metadata["recovery"] = recovery
    controller.store.save_operation(operation)


def _transition_operation(
    controller: OperationController,
    operation: Operation,
    target: OperationState,
    *,
    reason: str,
) -> None:
    current = operation.operation_state
    validate_transition(current, target)
    record = StateTransitionRecord(
        from_state=current.value,
        to_state=target.value,
        timestamp_utc=utc_now_iso(),
        reason=reason,
    )
    operation.history.append(record)
    operation.state = target.value
    operation.updated_at = utc_now_iso()
    event_id = controller.evidence.emit(
        operation_id=operation.id,
        event_type=EvidenceEventType.STATE_TRANSITION,
        correlation_id=operation.correlation_id,
        state_before=current.value,
        state_after=target.value,
        payload={"reason": reason, "recovery": True},
    )
    operation.evidence_event_ids.append(event_id)
    controller.store.save_operation(operation)


def _repair_terminal_receipts(
    controller: OperationController,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repairs: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for operation in controller.store.list_operations():
        if not operation_needs_receipt(operation, controller.store):
            continue
        result = ensure_terminal_receipt(controller, operation.id)
        if result.get("status") == "receipt_exported":
            repairs.append(result)
        elif result.get("status") == "recovery_blocker":
            blockers.append(result)
    return repairs, blockers


def _record_receipt_blocker(
    controller: OperationController,
    operation: Operation,
    *,
    reason: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    blocker = {
        "schema": "rexecop.recovery_blocker.v0.1",
        "reason": reason,
        "class": "evidence",
        "recorded_at": utc_now_iso(),
        "details": details,
    }
    operation.metadata["recovery_blocker"] = blocker
    controller.store.save_operation(operation)
    return {
        "operation_id": operation.id,
        "status": "recovery_blocker",
        "blocker": blocker,
    }
