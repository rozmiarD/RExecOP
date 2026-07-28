from __future__ import annotations

import fcntl
import json
import math
import sys
import unicodedata
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

from rexecop.errors import RExecOpConcurrencyConflict, RExecOpValidationError
from rexecop.operation.state import OperationState
from rexecop.runtime_ops.attempts import ATTEMPT_SCHEMA, ATTEMPT_STATUSES
from rexecop.runtime_ops.lease import WorkerLeaseManager
from rexecop.storage.atomic import atomic_write_text, secure_directory, secure_file
from rexecop.storage.port import RuntimeStore

QUEUE_CLAIM_RECOVERY_BLOCKED = "queue_claim_recovery_blocked"
QUEUE_CLAIM_LIFECYCLE_UNSUPPORTED = "queue_claim_lifecycle_unsupported"
INVALID_QUEUE_CLAIM_PARAMETERS = "invalid_queue_claim_parameters"
_TRANSITION_SCHEMA = "rexecop.queue_claim_transition.v0.1"
_TRANSITION_FIELDS = frozenset(
    {
        "private_schema",
        "disposition",
        "reason",
        "operation_state",
        "attempt_count",
        "attempt_status_counts",
        "claim_attempt",
        "prior_lease_epoch",
        "prior_process_instance_id",
        "current_lease_epoch",
        "current_process_instance_id",
        "recorded_at",
    }
)
_TRANSITION_DISPOSITIONS = frozenset({"blocked", "completed", "requeued"})
_TRANSITION_REASONS = frozenset(
    {
        "active_operation_requires_startup_recovery",
        "approved_operation_has_attempts",
        "attempt_inventory_unavailable",
        "claim_completed",
        "expired_pre_execution_claim",
        "invalid_claim_expiry",
        "invalid_queue_topology",
        "lease_epoch_not_advanced",
        "legacy_claim_not_expired",
        "malformed_attempt_inventory",
        "max_concurrent_reached",
        "operation_state_not_recoverable",
        "operation_unavailable",
        "target_locked",
        "terminal_operation",
        "unfinished_attempt_requires_recovery",
        "unknown_operation_state",
    }
)
_TRANSITION_OPERATION_STATES = frozenset(
    {item.value for item in OperationState} | {"unavailable", "unknown"}
)
_MAX_TRANSITION_ATTEMPT_COUNT = 1_000_000
_MAX_TRANSITION_EPOCH = (1 << 63) - 1
_MAX_TRANSITION_IDENTITY_LENGTH = 128
_MAX_TRANSITION_TIMESTAMP_LENGTH = 64
_PROCESS_IDENTITY_REDACTION_MARKERS = (
    "redacted-process-identity",
    "redacted-process-identity-2",
    "redacted-process-identity-3",
)
_ACTIVE_STATES = frozenset(
    {
        OperationState.RUNNING.value,
        OperationState.PAUSED.value,
        OperationState.RESUMING.value,
        OperationState.RETRYING.value,
        OperationState.VALIDATING.value,
    }
)
_TERMINAL_RECOVERY_STATES = frozenset(
    {
        OperationState.COMPLETED.value,
        OperationState.FAILED.value,
        OperationState.CANCELLED.value,
        OperationState.ESCALATED.value,
    }
)


class RunNowQueue:
    """Process-safe FIFO queue with durable, fenced claim records."""

    def __init__(self, store: RuntimeStore) -> None:
        self.store = store
        self.queue_dir = store.root / "queue"
        self.queue_file = self.queue_dir / "run_now.json"
        self.lock_file = self.queue_dir / "run_now.lock"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        secure_directory(self.queue_dir)
        with self.lock_file.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.queue_file.is_file():
            return {"pending": [], "claims": {}}
        try:
            secure_file(self.queue_file)
            data = json.loads(self.queue_file.read_text(encoding="utf-8"))
        except Exception:
            pass
        else:
            if not isinstance(data, dict):
                raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED)
            pending = data.get("pending")
            claims = data.get("claims")
            if not isinstance(pending, list) or (
                claims is not None and not isinstance(claims, dict)
            ):
                raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED)
            for claim in (claims or {}).values():
                if not isinstance(claim, dict):
                    continue
                if "last_transition" in claim and not _valid_transition_record(
                    claim["last_transition"]
                ):
                    raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED)
            return {"pending": list(pending), "claims": dict(claims or {})}
        raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED) from None

    def _save_unlocked(self, data: dict[str, Any]) -> None:
        data["updated_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
        atomic_write_text(
            self.queue_file,
            json.dumps(data, indent=2, sort_keys=True) + "\n",
        )

    def list_pending(self) -> list[str]:
        with self._locked():
            return list(self._load_unlocked()["pending"])

    def position(self, operation_id: str) -> int | None:
        pending = self.list_pending()
        return pending.index(operation_id) if operation_id in pending else None

    def enqueue(self, operation_id: str) -> int:
        with self._locked():
            data = self._load_unlocked()
            blocked = self._validate_topology(data)
            if blocked is not None:
                raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED)
            claim = data["claims"].get(operation_id)
            if isinstance(claim, dict) and claim.get("status") == "claimed":
                raise RExecOpConcurrencyConflict(
                    "concurrency_conflict: active queue claim exists "
                    f"for {operation_id}"
                )
            pending = data["pending"]
            if operation_id in pending:
                return pending.index(operation_id)
            pending.append(operation_id)
            self._save_unlocked(data)
            return pending.index(operation_id)

    def remove(self, operation_id: str) -> None:
        with self._locked():
            data = self._load_unlocked()
            self._validate_public_mutation(data)
            changed = (
                operation_id in data["pending"]
                or operation_id in data["claims"]
            )
            if not changed:
                return
            data["pending"] = [item for item in data["pending"] if item != operation_id]
            data["claims"].pop(operation_id, None)
            self._save_unlocked(data)

    def discard_pending(self, operation_id: str) -> None:
        with self._locked():
            data = self._load_unlocked()
            self._validate_public_mutation(data)
            if operation_id not in data["pending"]:
                return
            data["pending"] = [item for item in data["pending"] if item != operation_id]
            self._save_unlocked(data)

    def peek(self) -> str | None:
        pending = self.list_pending()
        return pending[0] if pending else None

    def claim(
        self,
        *,
        owner_token: str,
        lease_epoch: int,
        process_instance_id: str,
        ttl_seconds: float = 120.0,
    ) -> dict[str, Any] | None:
        """Compatibility wrapper preserving the original public claim API."""
        now = datetime.now(UTC).replace(microsecond=0)
        if (
            not _valid_public_claim_identity(owner_token)
            or not _valid_public_claim_identity(process_instance_id)
            or not _valid_bounded_integer(
                lease_epoch,
                maximum=_MAX_TRANSITION_EPOCH,
                minimum=1,
            )
        ):
            raise RExecOpValidationError(INVALID_QUEUE_CLAIM_PARAMETERS) from None
        expires_at = _validated_claim_expiry(ttl_seconds, observed_at=now)
        with self._locked():
            data = self._load_unlocked()
            blocked = self._validate_topology(data)
            if blocked is not None:
                raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED)
            claim = self._claim_unlocked(
                data,
                owner_token=owner_token,
                lease_epoch=lease_epoch,
                process_instance_id=process_instance_id,
                expires_at=expires_at,
                observed_at=now,
            )
            if claim is not None:
                self._save_unlocked(data)
            return claim

    def claim_from_lease(
        self,
        lease: Mapping[str, Any],
        *,
        ttl_seconds: float = 120.0,
    ) -> dict[str, Any] | None:
        """Recover and claim while exact worker-lease ownership is held."""
        now = datetime.now(UTC).replace(microsecond=0)
        expires_at = _validated_claim_expiry(ttl_seconds, observed_at=now)
        lease_guard = self._enter_lease_guard(lease)
        try:
            with self._locked():
                data = self._load_unlocked()
                recovered = self._recover_expired_unlocked(
                    data,
                    lease=lease,
                    observed_at=now,
                )
                claim = self._claim_unlocked(
                    data,
                    owner_token=str(lease.get("owner_token") or ""),
                    lease_epoch=int(lease.get("lease_epoch") or 0),
                    process_instance_id=str(
                        lease.get("process_instance_id") or ""
                    ),
                    expires_at=expires_at,
                    observed_at=now,
                )
                if recovered or claim is not None:
                    self._save_unlocked(data)
                return claim
        finally:
            lease_guard.__exit__(*sys.exc_info())

    def claim_specific_from_lease(
        self,
        operation_id: str,
        lease: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Claim one selected operation after classifying durable logical facts."""
        now = datetime.now(UTC).replace(microsecond=0)
        expires_at = _validated_claim_expiry(120.0, observed_at=now)
        lease_guard = self._enter_lease_guard(lease)
        try:
            with self._locked():
                data = self._load_unlocked()
                self._validate_strict_mutation_topology(data)
                previous = data["claims"].get(operation_id)
                if isinstance(previous, dict) and previous.get("status") == "claimed":
                    self._claim_conflict(operation_id)
                if operation_id not in data["pending"]:
                    return None
                state, attempts, load_reason = self._load_recovery_facts(operation_id)
                purpose = self._specific_claim_purpose(
                    state=state,
                    attempts=attempts,
                    load_reason=load_reason,
                )
                claim = self._claim_specific_unlocked(
                    data,
                    operation_id=operation_id,
                    owner_token=str(lease.get("owner_token") or ""),
                    lease_epoch=int(lease.get("lease_epoch") or 0),
                    process_instance_id=str(
                        lease.get("process_instance_id") or ""
                    ),
                    expires_at=expires_at,
                    observed_at=now,
                )
                self._save_unlocked(data)
                return {"purpose": purpose, "claim": dict(claim)}
        finally:
            lease_guard.__exit__(*sys.exc_info())

    def _claim_unlocked(
        self,
        data: dict[str, Any],
        *,
        owner_token: str,
        lease_epoch: int,
        process_instance_id: str,
        expires_at: datetime,
        observed_at: datetime,
    ) -> dict[str, Any] | None:
        pending = data["pending"]
        if not pending:
            return None
        operation_id = pending[0]
        previous = data["claims"].get(operation_id)
        if isinstance(previous, dict) and previous.get("status") == "claimed":
            raise RExecOpConcurrencyConflict(
                "concurrency_conflict: active queue claim exists "
                f"for {operation_id}"
            )
        attempt = self._next_attempt(previous)
        claim = {
            "operation_id": operation_id,
            "status": "claimed",
            "owner_token": owner_token,
            "process_instance_id": process_instance_id,
            "lease_epoch": lease_epoch,
            "attempt": attempt,
            "claimed_at": observed_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        if isinstance(previous, dict) and isinstance(
            previous.get("last_transition"), dict
        ):
            claim["last_transition"] = dict(previous["last_transition"])
        pending.pop(0)
        data["claims"][operation_id] = claim
        return claim

    def _claim_specific_unlocked(
        self,
        data: dict[str, Any],
        *,
        operation_id: str,
        owner_token: str,
        lease_epoch: int,
        process_instance_id: str,
        expires_at: datetime,
        observed_at: datetime,
    ) -> dict[str, Any]:
        previous = data["claims"].get(operation_id)
        attempt = self._next_attempt(previous)
        claim = {
            "operation_id": operation_id,
            "status": "claimed",
            "owner_token": owner_token,
            "process_instance_id": process_instance_id,
            "lease_epoch": lease_epoch,
            "attempt": attempt,
            "claimed_at": observed_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        if isinstance(previous, dict) and isinstance(
            previous.get("last_transition"), dict
        ):
            claim["last_transition"] = dict(previous["last_transition"])
        data["pending"].remove(operation_id)
        data["claims"][operation_id] = claim
        return claim

    def complete_claim(
        self,
        operation_id: str,
        *,
        owner_token: str,
        lease_epoch: int,
    ) -> None:
        """Compatibility wrapper preserving the original public completion API."""
        with self._locked():
            data = self._load_unlocked()
            claim = data["claims"].get(operation_id)
            if not isinstance(claim, dict) or (
                claim.get("status") != "claimed"
                or str(claim.get("owner_token") or "") != owner_token
                or int(claim.get("lease_epoch") or 0) != lease_epoch
            ):
                self._claim_conflict(operation_id)
            claim["status"] = "completed"
            claim["completed_at"] = (
                datetime.now(UTC).replace(microsecond=0).isoformat()
            )
            data["claims"][operation_id] = claim
            self._save_unlocked(data)

    def complete_claim_from_lease(
        self,
        operation_id: str,
        lease: Mapping[str, Any],
        *,
        claim_snapshot: Mapping[str, Any] | None = None,
    ) -> None:
        lease_guard = self._enter_lease_guard(
            lease,
            completion_operation_id=operation_id,
        )
        try:
            with self._locked():
                data = self._load_unlocked()
                claim = data["claims"].get(operation_id)
                if not self._exact_current_claim(
                    claim,
                    operation_id=operation_id,
                    lease=lease,
                    claim_snapshot=claim_snapshot,
                ):
                    self._claim_conflict(operation_id)
                observed_at = datetime.now(UTC).replace(microsecond=0)
                claim["status"] = "completed"
                claim["completed_at"] = observed_at.isoformat()
                claim["last_transition"] = self._transition_record(
                    claim,
                    lease=lease,
                    observed_at=observed_at,
                    disposition="completed",
                    reason="claim_completed",
                    operation_state="unavailable",
                    attempts=[],
                )
                data["claims"][operation_id] = claim
                self._save_unlocked(data)
        finally:
            lease_guard.__exit__(*sys.exc_info())

    def _complete_and_remove_claim_from_lease(
        self,
        operation_id: str,
        lease: Mapping[str, Any],
        *,
        claim_snapshot: Mapping[str, Any],
    ) -> None:
        lease_guard = self._enter_lease_guard(
            lease,
            completion_operation_id=operation_id,
        )
        try:
            with self._locked():
                data = self._load_unlocked()
                self._validate_strict_mutation_topology(data)
                if operation_id in data["pending"]:
                    raise RExecOpValidationError(
                        QUEUE_CLAIM_RECOVERY_BLOCKED
                    ) from None
                current = data["claims"].get(operation_id)
                if (
                    claim_snapshot.get("status") != "claimed"
                    or not self._exact_current_claim(
                        current,
                        operation_id=operation_id,
                        lease=lease,
                        claim_snapshot=claim_snapshot,
                    )
                ):
                    self._claim_conflict(operation_id)
                data["claims"].pop(operation_id)
                self._save_unlocked(data)
        finally:
            lease_guard.__exit__(*sys.exc_info())

    def defer_claim_from_lease(
        self,
        operation_id: str,
        claim_snapshot: Mapping[str, Any],
        lease: Mapping[str, Any],
        *,
        reason: str,
    ) -> None:
        if reason not in {"max_concurrent_reached", "target_locked"}:
            raise RExecOpValidationError("unsupported queue deferral reason")
        lease_guard = self._enter_lease_guard(
            lease,
            completion_operation_id=operation_id,
        )
        try:
            with self._locked():
                data = self._load_unlocked()
                current = data["claims"].get(operation_id)
                if self._same_defer_is_complete(
                    data,
                    operation_id=operation_id,
                    current=current,
                    claim_snapshot=claim_snapshot,
                    lease=lease,
                    reason=reason,
                ):
                    return
                if not self._exact_current_claim(
                    current,
                    operation_id=operation_id,
                    lease=lease,
                    claim_snapshot=claim_snapshot,
                ):
                    self._claim_conflict(operation_id)
                state, attempts, load_reason = self._load_recovery_facts(operation_id)
                if load_reason or state != OperationState.APPROVED.value or attempts:
                    self._claim_conflict(operation_id)
                if operation_id in data["pending"]:
                    self._claim_conflict(operation_id)
                observed_at = datetime.now(UTC).replace(microsecond=0)
                current["status"] = "requeued"
                current["last_transition"] = self._transition_record(
                    current,
                    lease=lease,
                    observed_at=observed_at,
                    disposition="requeued",
                    reason=reason,
                    operation_state=state,
                    attempts=attempts,
                )
                data["pending"].append(operation_id)
                data["claims"][operation_id] = current
                self._save_unlocked(data)
        finally:
            lease_guard.__exit__(*sys.exc_info())

    def remove_cancelled_from_lease(
        self,
        operation_id: str,
        lease: Mapping[str, Any],
    ) -> None:
        """Remove queue state only after durable, attempt-safe cancellation."""
        lease_guard = self._enter_lease_guard(lease)
        try:
            with self._locked():
                data = self._load_unlocked()
                self._validate_strict_mutation_topology(data)
                state, attempts, load_reason = self._load_recovery_facts(operation_id)
                if (
                    load_reason
                    or state != OperationState.CANCELLED.value
                    or any(
                        attempt.get("status") in {"pending", "started"}
                        for attempt in attempts
                    )
                ):
                    raise RExecOpValidationError(
                        QUEUE_CLAIM_RECOVERY_BLOCKED
                    ) from None
                changed = (
                    operation_id in data["pending"]
                    or operation_id in data["claims"]
                )
                if not changed:
                    return
                data["pending"] = [
                    item for item in data["pending"] if item != operation_id
                ]
                data["claims"].pop(operation_id, None)
                self._save_unlocked(data)
        finally:
            lease_guard.__exit__(*sys.exc_info())

    def recover_expired_claims_from_lease(
        self,
        lease: Mapping[str, Any],
        *,
        observed_at: datetime | None = None,
    ) -> bool:
        recovery_time = observed_at or datetime.now(UTC).replace(microsecond=0)
        lease_guard = self._enter_lease_guard(lease)
        try:
            with self._locked():
                data = self._load_unlocked()
                changed = self._recover_expired_unlocked(
                    data,
                    lease=lease,
                    observed_at=recovery_time,
                )
                if changed:
                    self._save_unlocked(data)
                return changed
        finally:
            lease_guard.__exit__(*sys.exc_info())

    def _recover_expired_unlocked(
        self,
        data: dict[str, Any],
        *,
        lease: Mapping[str, Any],
        observed_at: datetime,
    ) -> bool:
        blocked_operation = self._validate_topology(data)
        if blocked_operation is not None:
            if self._persist_blocker_if_possible(
                data,
                blocked_operation,
                lease=lease,
                observed_at=observed_at,
                reason="invalid_queue_topology",
            ):
                self._save_unlocked(data)
            raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED)

        changed = False
        pending = data["pending"]
        for operation_id in sorted(data["claims"]):
            claim = data["claims"][operation_id]
            status = str(claim.get("status") or "")
            if status in {"completed", "requeued"}:
                continue
            legacy_pending = operation_id in pending
            expiry = _parse_time(str(claim.get("expires_at") or ""))
            if expiry is None:
                self._block_claim(
                    data,
                    operation_id,
                    claim,
                    lease=lease,
                    observed_at=observed_at,
                    reason="invalid_claim_expiry",
                )
            if observed_at < expiry:
                if legacy_pending:
                    self._block_claim(
                        data,
                        operation_id,
                        claim,
                        lease=lease,
                        observed_at=observed_at,
                        reason="legacy_claim_not_expired",
                    )
                continue
            prior_epoch = int(claim["lease_epoch"])
            current_epoch = int(lease.get("lease_epoch") or 0)
            if current_epoch <= prior_epoch:
                self._block_claim(
                    data,
                    operation_id,
                    claim,
                    lease=lease,
                    observed_at=observed_at,
                    reason="lease_epoch_not_advanced",
                )

            operation_state, attempts, load_reason = self._load_recovery_facts(
                operation_id
            )
            if load_reason:
                self._block_claim(
                    data,
                    operation_id,
                    claim,
                    lease=lease,
                    observed_at=observed_at,
                    reason=load_reason,
                    operation_state=operation_state,
                    attempts=attempts,
                )
            if operation_state == OperationState.APPROVED.value and not attempts:
                claim["status"] = "requeued"
                claim["last_transition"] = self._transition_record(
                    claim,
                    lease=lease,
                    observed_at=observed_at,
                    disposition="requeued",
                    reason="expired_pre_execution_claim",
                    operation_state=operation_state,
                    attempts=attempts,
                )
                if not legacy_pending:
                    pending.append(operation_id)
                data["claims"][operation_id] = claim
                changed = True
                continue
            if operation_state == OperationState.APPROVED.value:
                self._block_claim(
                    data,
                    operation_id,
                    claim,
                    lease=lease,
                    observed_at=observed_at,
                    reason="approved_operation_has_attempts",
                    operation_state=operation_state,
                    attempts=attempts,
                )
            if legacy_pending:
                self._block_claim(
                    data,
                    operation_id,
                    claim,
                    lease=lease,
                    observed_at=observed_at,
                    reason="operation_state_not_recoverable",
                    operation_state=operation_state,
                    attempts=attempts,
                )
            if operation_state in _ACTIVE_STATES:
                self._block_claim(
                    data,
                    operation_id,
                    claim,
                    lease=lease,
                    observed_at=observed_at,
                    reason="active_operation_requires_startup_recovery",
                    operation_state=operation_state,
                    attempts=attempts,
                )
            if operation_state in _TERMINAL_RECOVERY_STATES:
                if any(item["status"] in {"pending", "started"} for item in attempts):
                    self._block_claim(
                        data,
                        operation_id,
                        claim,
                        lease=lease,
                        observed_at=observed_at,
                        reason="unfinished_attempt_requires_recovery",
                        operation_state=operation_state,
                        attempts=attempts,
                    )
                claim["status"] = "completed"
                claim["completed_at"] = observed_at.isoformat()
                claim["last_transition"] = self._transition_record(
                    claim,
                    lease=lease,
                    observed_at=observed_at,
                    disposition="completed",
                    reason="terminal_operation",
                    operation_state=operation_state,
                    attempts=attempts,
                )
                data["claims"][operation_id] = claim
                changed = True
                continue
            self._block_claim(
                data,
                operation_id,
                claim,
                lease=lease,
                observed_at=observed_at,
                reason="operation_state_not_recoverable",
                operation_state=operation_state,
                attempts=attempts,
            )
        return changed

    def _validate_topology(self, data: dict[str, Any]) -> str | None:
        pending = data["pending"]
        claims = data["claims"]
        if any(not isinstance(item, str) or not item for item in pending):
            return ""
        if len(set(pending)) != len(pending):
            return ""
        pending_set = set(pending)
        for operation_id, claim in claims.items():
            if (
                not isinstance(operation_id, str)
                or not operation_id
                or not isinstance(claim, dict)
            ):
                return operation_id if isinstance(operation_id, str) else ""
            status = claim.get("status")
            if status not in {"claimed", "completed", "requeued"}:
                return operation_id
            if str(claim.get("operation_id") or "") != operation_id:
                return operation_id
            if not self._valid_claim_identity(claim):
                return operation_id
            is_pending = operation_id in pending_set
            if status == "requeued" and not is_pending:
                return operation_id
        return None

    def _validate_strict_mutation_topology(self, data: dict[str, Any]) -> None:
        if self._validate_topology(data) is not None:
            raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED) from None
        pending = set(data["pending"])
        if any(
            claim.get("status") == "claimed" and operation_id in pending
            for operation_id, claim in data["claims"].items()
        ):
            raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED) from None

    def _validate_public_mutation(self, data: dict[str, Any]) -> None:
        self._validate_strict_mutation_topology(data)
        for operation_id in sorted(data["claims"]):
            claim = data["claims"][operation_id]
            if claim.get("status") in {"claimed", "requeued"}:
                self._claim_conflict(operation_id)

    @staticmethod
    def _specific_claim_purpose(
        *,
        state: str,
        attempts: list[dict[str, Any]],
        load_reason: str,
    ) -> str:
        if load_reason:
            raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED) from None
        if state == OperationState.APPROVED.value and not attempts:
            return "execution"
        if state in _TERMINAL_RECOVERY_STATES and not any(
            attempt.get("status") in {"pending", "started"}
            for attempt in attempts
        ):
            return "terminal_cleanup"
        raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED) from None

    @staticmethod
    def _valid_claim_identity(claim: Mapping[str, Any]) -> bool:
        epoch = claim.get("lease_epoch")
        attempt = claim.get("attempt")
        return (
            _valid_bounded_integer(
                epoch,
                maximum=_MAX_TRANSITION_EPOCH,
                minimum=1,
            )
            and _valid_bounded_integer(
                attempt,
                maximum=_MAX_TRANSITION_EPOCH,
                minimum=1,
            )
            and isinstance(claim.get("owner_token"), str)
            and bool(claim["owner_token"])
            and isinstance(claim.get("process_instance_id"), str)
            and bool(claim["process_instance_id"])
        )

    def _load_recovery_facts(
        self,
        operation_id: str,
    ) -> tuple[str, list[dict[str, Any]], str]:
        loader = getattr(self.store, "_queue_claim_facts", None)
        if not callable(loader):
            return "unavailable", [], "attempt_inventory_unavailable"
        try:
            operation, attempts = loader(operation_id)
        except Exception:
            return "unavailable", [], "operation_unavailable"
        operation_state = str(operation.state or "")
        if operation_state not in {item.value for item in OperationState}:
            return "unknown", [], "unknown_operation_state"
        if not isinstance(attempts, list):
            return operation_state, [], "malformed_attempt_inventory"
        seen: set[str] = set()
        for attempt in attempts:
            if not isinstance(attempt, dict):
                return operation_state, [], "malformed_attempt_inventory"
            attempt_id = attempt.get("attempt_id")
            if (
                attempt.get("schema") != ATTEMPT_SCHEMA
                or not isinstance(attempt_id, str)
                or not attempt_id
                or attempt_id in seen
                or str(attempt.get("operation_id") or "") != operation_id
                or attempt.get("status") not in ATTEMPT_STATUSES
            ):
                return operation_state, attempts, "malformed_attempt_inventory"
            seen.add(attempt_id)
        return operation_state, attempts, ""

    def _block_claim(
        self,
        data: dict[str, Any],
        operation_id: str,
        claim: dict[str, Any],
        *,
        lease: Mapping[str, Any],
        observed_at: datetime,
        reason: str,
        operation_state: str = "unavailable",
        attempts: list[dict[str, Any]] | None = None,
    ) -> NoReturn:
        claim["last_transition"] = self._transition_record(
            claim,
            lease=lease,
            observed_at=observed_at,
            disposition="blocked",
            reason=reason,
            operation_state=operation_state,
            attempts=attempts or [],
        )
        data["claims"][operation_id] = claim
        self._save_unlocked(data)
        raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED)

    def _persist_blocker_if_possible(
        self,
        data: dict[str, Any],
        operation_id: str,
        *,
        lease: Mapping[str, Any],
        observed_at: datetime,
        reason: str,
    ) -> bool:
        claim = data["claims"].get(operation_id)
        if not isinstance(claim, dict) or not self._valid_claim_identity(claim):
            return False
        claim["last_transition"] = self._transition_record(
            claim,
            lease=lease,
            observed_at=observed_at,
            disposition="blocked",
            reason=reason,
            operation_state="unavailable",
            attempts=[],
        )
        return True

    @staticmethod
    def _transition_record(
        claim: Mapping[str, Any],
        *,
        lease: Mapping[str, Any],
        observed_at: datetime,
        disposition: str,
        reason: str,
        operation_state: str,
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        remaining = _MAX_TRANSITION_ATTEMPT_COUNT
        status_counts: dict[str, int] = {}
        for status in sorted(ATTEMPT_STATUSES):
            count = min(
                sum(1 for attempt in attempts if attempt.get("status") == status),
                remaining,
            )
            status_counts[status] = count
            remaining -= count
        prior_owner_token = str(claim.get("owner_token") or "")
        current_owner_token = str(lease.get("owner_token") or "")
        return {
            "private_schema": _TRANSITION_SCHEMA,
            "disposition": disposition,
            "reason": reason,
            "operation_state": operation_state,
            "attempt_count": sum(status_counts.values()),
            "attempt_status_counts": status_counts,
            "claim_attempt": _bounded_epoch(claim.get("attempt")),
            "prior_lease_epoch": _bounded_epoch(claim.get("lease_epoch")),
            "prior_process_instance_id": _project_process_identity(
                claim.get("process_instance_id"),
                prior_owner_token=prior_owner_token,
                current_owner_token=current_owner_token,
            ),
            "current_lease_epoch": _bounded_epoch(lease.get("lease_epoch")),
            "current_process_instance_id": _project_process_identity(
                lease.get("process_instance_id"),
                prior_owner_token=prior_owner_token,
                current_owner_token=current_owner_token,
            ),
            "recorded_at": observed_at.isoformat(),
        }

    def _enter_lease_guard(
        self,
        lease: Mapping[str, Any],
        *,
        completion_operation_id: str | None = None,
    ) -> AbstractContextManager[None]:
        try:
            guard = WorkerLeaseManager(self.store.root).guard(lease)
            guard.__enter__()
        except Exception:  # noqa: BLE001 - redact private lease-store failures
            if completion_operation_id is not None:
                self._claim_conflict(completion_operation_id)
            raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED) from None
        return guard

    @staticmethod
    def _exact_current_claim(
        current: Any,
        *,
        operation_id: str,
        lease: Mapping[str, Any],
        claim_snapshot: Mapping[str, Any] | None,
    ) -> bool:
        if not isinstance(current, dict) or current.get("status") != "claimed":
            return False
        expected = claim_snapshot or current
        fields = (
            "operation_id",
            "owner_token",
            "process_instance_id",
            "lease_epoch",
            "attempt",
        )
        return (
            str(current.get("operation_id") or "") == operation_id
            and all(current.get(field) == expected.get(field) for field in fields)
            and str(current.get("owner_token") or "")
            == str(lease.get("owner_token") or "")
            and int(current.get("lease_epoch") or 0)
            == int(lease.get("lease_epoch") or 0)
            and str(current.get("process_instance_id") or "")
            == str(lease.get("process_instance_id") or "")
        )

    @staticmethod
    def _same_defer_is_complete(
        data: Mapping[str, Any],
        *,
        operation_id: str,
        current: Any,
        claim_snapshot: Mapping[str, Any],
        lease: Mapping[str, Any],
        reason: str,
    ) -> bool:
        if not isinstance(current, dict) or current.get("status") != "requeued":
            return False
        transition = current.get("last_transition")
        if not isinstance(transition, dict):
            return False
        pending = data.get("pending")
        prior_process_instance_id = _project_process_identity(
            claim_snapshot.get("process_instance_id"),
            prior_owner_token=str(claim_snapshot.get("owner_token") or ""),
            current_owner_token=str(lease.get("owner_token") or ""),
        )
        current_process_instance_id = _project_process_identity(
            lease.get("process_instance_id"),
            prior_owner_token=str(claim_snapshot.get("owner_token") or ""),
            current_owner_token=str(lease.get("owner_token") or ""),
        )
        return (
            isinstance(pending, list)
            and pending.count(operation_id) == 1
            and str(current.get("operation_id") or "") == operation_id
            and current.get("owner_token") == claim_snapshot.get("owner_token")
            and current.get("process_instance_id")
            == claim_snapshot.get("process_instance_id")
            and current.get("lease_epoch") == claim_snapshot.get("lease_epoch")
            and current.get("attempt") == claim_snapshot.get("attempt")
            and current.get("owner_token") == lease.get("owner_token")
            and current.get("process_instance_id")
            == lease.get("process_instance_id")
            and current.get("lease_epoch") == lease.get("lease_epoch")
            and transition.get("disposition") == "requeued"
            and transition.get("reason") == reason
            and transition.get("claim_attempt") == claim_snapshot.get("attempt")
            and transition.get("prior_lease_epoch")
            == claim_snapshot.get("lease_epoch")
            and transition.get("prior_process_instance_id")
            == prior_process_instance_id
            and transition.get("current_lease_epoch") == lease.get("lease_epoch")
            and transition.get("current_process_instance_id")
            == current_process_instance_id
        )

    @staticmethod
    def _claim_conflict(operation_id: str) -> NoReturn:
        raise RExecOpConcurrencyConflict(
            "concurrency_conflict: queue claim ownership lost "
            f"for {operation_id}"
        ) from None

    @staticmethod
    def _next_attempt(previous: Any) -> int:
        if not isinstance(previous, dict):
            return 1
        attempt = previous.get("attempt")
        if not isinstance(attempt, int) or not _valid_bounded_integer(
            attempt,
            maximum=_MAX_TRANSITION_EPOCH - 1,
            minimum=1,
        ):
            raise RExecOpValidationError(QUEUE_CLAIM_RECOVERY_BLOCKED)
        return attempt + 1

    def dequeue(self) -> str | None:
        """Compatibility helper; execution paths must use fenced claim()."""
        with self._locked():
            data = self._load_unlocked()
            self._validate_public_mutation(data)
            if not data["pending"]:
                return None
            operation_id = data["pending"].pop(0)
            self._save_unlocked(data)
            return operation_id


def _parse_time(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _valid_public_claim_identity(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_TRANSITION_IDENTITY_LENGTH
        and not any(unicodedata.category(character).startswith("C") for character in value)
    )


def _validated_claim_expiry(value: Any, *, observed_at: datetime) -> datetime:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RExecOpValidationError(INVALID_QUEUE_CLAIM_PARAMETERS) from None
    try:
        ttl_seconds = float(value)
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError
        expires_at = observed_at + timedelta(seconds=ttl_seconds)
    except (OverflowError, TypeError, ValueError):
        raise RExecOpValidationError(INVALID_QUEUE_CLAIM_PARAMETERS) from None
    if expires_at <= observed_at:
        raise RExecOpValidationError(INVALID_QUEUE_CLAIM_PARAMETERS) from None
    return expires_at


def _bounded_identity(value: str) -> str:
    if (
        not value
        or len(value) > _MAX_TRANSITION_IDENTITY_LENGTH
        or any(character in value for character in "\r\n")
    ):
        return "invalid"
    return value


def _project_process_identity(
    value: Any,
    *,
    prior_owner_token: str,
    current_owner_token: str,
) -> str:
    bounded = _bounded_identity(str(value or ""))
    owner_tokens = {prior_owner_token, current_owner_token}
    if bounded not in owner_tokens:
        return bounded
    for marker in _PROCESS_IDENTITY_REDACTION_MARKERS:
        if marker not in owner_tokens:
            return marker
    raise AssertionError("bounded process identity marker set exhausted")


def _bounded_epoch(value: Any) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > _MAX_TRANSITION_EPOCH
    ):
        return 0
    return value


def _valid_transition_record(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _TRANSITION_FIELDS:
        return False
    if value.get("private_schema") != _TRANSITION_SCHEMA:
        return False
    if value.get("disposition") not in _TRANSITION_DISPOSITIONS:
        return False
    if value.get("reason") not in _TRANSITION_REASONS:
        return False
    if value.get("operation_state") not in _TRANSITION_OPERATION_STATES:
        return False
    attempt_count = value.get("attempt_count")
    if not _valid_bounded_integer(
        attempt_count,
        maximum=_MAX_TRANSITION_ATTEMPT_COUNT,
    ):
        return False
    status_counts = value.get("attempt_status_counts")
    if not isinstance(status_counts, dict) or set(status_counts) != ATTEMPT_STATUSES:
        return False
    if not all(
        _valid_bounded_integer(count, maximum=_MAX_TRANSITION_ATTEMPT_COUNT)
        for count in status_counts.values()
    ):
        return False
    if sum(status_counts.values()) != attempt_count:
        return False
    for field in ("claim_attempt", "prior_lease_epoch", "current_lease_epoch"):
        if not _valid_bounded_integer(
            value.get(field),
            maximum=_MAX_TRANSITION_EPOCH,
            minimum=1,
        ):
            return False
    for field in ("prior_process_instance_id", "current_process_instance_id"):
        identity = value.get(field)
        if (
            not isinstance(identity, str)
            or not identity
            or len(identity) > _MAX_TRANSITION_IDENTITY_LENGTH
            or any(character in identity for character in "\r\n")
        ):
            return False
    recorded_at = value.get("recorded_at")
    return isinstance(recorded_at, str) and _valid_transition_timestamp(recorded_at)


def _valid_bounded_integer(
    value: Any,
    *,
    maximum: int,
    minimum: int = 0,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _valid_transition_timestamp(value: str) -> bool:
    if not value or len(value) > _MAX_TRANSITION_TIMESTAMP_LENGTH:
        return False
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    return parsed.tzinfo is not None


class StoreRunNowQueue:
    """Compatibility facade that keeps lifecycle code on the RuntimeStore port."""

    def __init__(self, store: RuntimeStore) -> None:
        self.store = store

    def list_pending(self) -> list[str]:
        return self._lifecycle().list_pending()

    def position(self, operation_id: str) -> int | None:
        return self._lifecycle().position(operation_id)

    def enqueue(self, operation_id: str) -> int:
        return self._lifecycle().enqueue(operation_id)

    def remove(self, operation_id: str) -> None:
        self._lifecycle().remove(operation_id)

    def discard_pending(self, operation_id: str) -> None:
        self._lifecycle().discard_pending(operation_id)

    def claim_from_lease(self, lease: dict[str, Any]) -> dict[str, Any] | None:
        return self._lifecycle().claim_from_lease(lease)

    def claim_specific_from_lease(
        self,
        operation_id: str,
        lease: dict[str, Any],
    ) -> dict[str, Any] | None:
        return self._lifecycle().claim_specific_from_lease(operation_id, lease)

    def complete_claim_from_lease(
        self,
        operation_id: str,
        lease: dict[str, Any],
        *,
        claim_snapshot: Mapping[str, Any] | None = None,
    ) -> None:
        self._lifecycle().complete_claim_from_lease(
            operation_id,
            lease,
            claim_snapshot=claim_snapshot,
        )

    def _complete_and_remove_claim_from_lease(
        self,
        operation_id: str,
        lease: dict[str, Any],
        *,
        claim_snapshot: Mapping[str, Any],
    ) -> None:
        self._lifecycle()._complete_and_remove_claim_from_lease(
            operation_id,
            lease,
            claim_snapshot=claim_snapshot,
        )

    def defer_claim_from_lease(
        self,
        operation_id: str,
        claim_snapshot: Mapping[str, Any],
        lease: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        self._lifecycle().defer_claim_from_lease(
            operation_id,
            claim_snapshot,
            lease,
            reason=reason,
        )

    def remove_cancelled_from_lease(
        self,
        operation_id: str,
        lease: dict[str, Any],
    ) -> None:
        self._lifecycle().remove_cancelled_from_lease(operation_id, lease)

    def recover_expired_claims_from_lease(
        self,
        lease: dict[str, Any],
        *,
        observed_at: datetime | None = None,
    ) -> bool:
        return self._lifecycle().recover_expired_claims_from_lease(
            lease,
            observed_at=observed_at,
        )

    def _lifecycle(self) -> RunNowQueue:
        from rexecop.storage.file_store import FileStore
        from rexecop.storage.memory_store import InMemoryStore
        from rexecop.storage.sqlite_store import SqliteStore

        if type(self.store) not in {FileStore, InMemoryStore, SqliteStore}:
            self._unsupported()
        lifecycle_factory = getattr(self.store, "_queue_claim_lifecycle", None)
        facts = getattr(self.store, "_queue_claim_facts", None)
        if (
            not callable(lifecycle_factory)
            or getattr(lifecycle_factory, "__self__", None) is not self.store
            or not callable(facts)
            or getattr(facts, "__self__", None) is not self.store
        ):
            self._unsupported()
        try:
            lifecycle = lifecycle_factory()
        except Exception:
            self._unsupported()
        if type(lifecycle) is not RunNowQueue or lifecycle.store is not self.store:
            self._unsupported()
        return lifecycle

    @staticmethod
    def _unsupported() -> NoReturn:
        raise RExecOpValidationError(QUEUE_CLAIM_LIFECYCLE_UNSUPPORTED) from None
