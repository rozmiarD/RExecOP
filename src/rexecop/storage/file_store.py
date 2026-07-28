from __future__ import annotations

import fcntl
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rexecop.errors import (
    RExecOpConcurrencyConflict,
    RExecOpValidationError,
    _InvalidJsonValue,
)
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.storage.atomic import atomic_write_text, secure_directory, secure_file
from rexecop.yaml_input import ensure_finite_json_value


class FileStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".rexecop"
        self.operations_dir = self.root / "operations"
        self.plans_dir = self.root / "plans"
        self.evidence_dir = self.root / "evidence"
        self.receipts_dir = self.root / "receipts"
        self.sclite_dir = self.root / "sclite"
        self.approvals_dir = self.root / "approvals"
        self.observability_dir = self.root / "observability" / "events"
        self.permits_dir = self.root / "permits"
        self.governance_claims_dir = self.root / "governance_claims"

    def ensure_layout(self) -> None:
        secure_directory(self.root)
        for path in (
            self.operations_dir,
            self.plans_dir,
            self.evidence_dir,
            self.receipts_dir,
            self.sclite_dir,
            self.approvals_dir,
            self.observability_dir,
            self.permits_dir,
            self.governance_claims_dir,
        ):
            secure_directory(path)

    def operation_sclite_dir(self, operation_id: str) -> Path:
        self.ensure_layout()
        path = self.sclite_dir / operation_id
        secure_directory(path)
        return path

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        ensure_finite_json_value(payload)
        try:
            rendered = json.dumps(
                payload,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise _InvalidJsonValue() from exc
        atomic_write_text(path, rendered + "\n")

    def save_operation(self, operation: Operation) -> None:
        ensure_finite_json_value(operation.as_dict())
        self.ensure_layout()
        path = self.operations_dir / f"{operation.id}.json"
        lock_path = self.operations_dir / f"{operation.id}.lock"
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            current_revision = 0
            if path.is_file():
                current = json.loads(path.read_text(encoding="utf-8"))
                current_revision = int(current.get("operation_revision") or 0)
                if current_revision != operation.operation_revision:
                    raise RExecOpConcurrencyConflict(
                        f"concurrency_conflict: operation {operation.id} expected revision "
                        f"{operation.operation_revision}, found {current_revision}"
                    )
            elif operation.operation_revision != 0:
                raise RExecOpConcurrencyConflict(
                    f"concurrency_conflict: operation {operation.id} no longer exists"
                )
            next_revision = current_revision + 1
            payload = operation.as_dict()
            payload["operation_revision"] = next_revision
            self._write_json(path, payload)
            operation.operation_revision = next_revision
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def load_operation(self, operation_id: str) -> Operation:
        path = self.operations_dir / f"{operation_id}.json"
        if not path.is_file():
            raise RExecOpValidationError(f"operation not found: {operation_id}")
        data = json.loads(path.read_text())
        return Operation.from_dict(data)

    def list_operations(self) -> list[Operation]:
        self.ensure_layout()
        operations: list[Operation] = []
        for path in sorted(self.operations_dir.glob("*.json")):
            operations.append(Operation.from_dict(json.loads(path.read_text())))
        return operations

    def save_plan(self, plan: OperationPlan) -> None:
        self.ensure_layout()
        path = self.plans_dir / f"{plan.operation_id}.json"
        self._write_json(path, plan.as_dict())

    def load_plan(self, operation_id: str) -> OperationPlan:
        path = self.plans_dir / f"{operation_id}.json"
        if not path.is_file():
            raise RExecOpValidationError(f"operation plan not found: {operation_id}")
        data = json.loads(path.read_text())
        return OperationPlan.from_dict(data)

    def save_evidence_event(self, operation_id: str, event: dict[str, Any]) -> None:
        self.ensure_layout()
        event_id = str(event["event_id"])
        op_dir = self.evidence_dir / operation_id
        secure_directory(op_dir)
        path = op_dir / f"{event_id}.json"
        self._write_json(path, event)

    def list_evidence_events(self, operation_id: str) -> list[dict[str, Any]]:
        op_dir = self.evidence_dir / operation_id
        if not op_dir.is_dir():
            return []
        events: list[dict[str, Any]] = []
        for path in sorted(op_dir.glob("*.json")):
            events.append(json.loads(path.read_text()))
        return events

    def save_structured_log_event(self, event: dict[str, Any]) -> None:
        self.ensure_layout()
        event_id = str(event["event_id"])
        path = self.observability_dir / f"{event_id}.json"
        self._write_json(path, event)

    def list_structured_log_events(
        self,
        *,
        operation_id: str | None = None,
        correlation_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self.ensure_layout()
        if not self.observability_dir.is_dir():
            return []
        bounded_limit = max(1, min(int(limit), 200))
        items: list[dict[str, Any]] = []
        for path in sorted(self.observability_dir.glob("*.json"), reverse=True):
            event = json.loads(path.read_text())
            refs = event.get("refs")
            if operation_id and (
                not isinstance(refs, dict) or str(refs.get("operation_id") or "") != operation_id
            ):
                continue
            if correlation_id and str(event.get("correlation_id") or "") != correlation_id:
                continue
            items.append(event)
            if len(items) >= bounded_limit:
                break
        items.reverse()
        return items

    def save_receipt_export(self, operation_id: str, export: dict[str, Any]) -> Path:
        self.ensure_layout()
        path = self.receipts_dir / f"{operation_id}.json"
        self._write_json(path, export)
        return path

    def load_receipt_export(self, operation_id: str) -> dict[str, Any]:
        path = self.receipts_dir / f"{operation_id}.json"
        if not path.is_file():
            raise RExecOpValidationError(f"receipt export not found: {operation_id}")
        return json.loads(path.read_text())

    def save_approval(self, operation_id: str, approval: dict[str, Any]) -> Path:
        self.ensure_layout()
        path = self.approvals_dir / f"{operation_id}.json"
        self._write_json(path, approval)
        return path

    def load_approval(self, operation_id: str) -> dict[str, Any]:
        path = self.approvals_dir / f"{operation_id}.json"
        if not path.is_file():
            raise RExecOpValidationError(f"approval not found: {operation_id}")
        return json.loads(path.read_text())

    def acquire_execution_lease(self, *, worker_id: str) -> dict[str, Any]:
        from rexecop.runtime_ops.lease import WorkerLeaseManager

        return WorkerLeaseManager(self.root).acquire(worker_id=worker_id)

    def renew_execution_lease(self, lease: dict[str, Any]) -> dict[str, Any]:
        from rexecop.runtime_ops.lease import WorkerLeaseManager

        return WorkerLeaseManager(self.root).renew(
            owner_token=str(lease["owner_token"]),
            lease_epoch=int(lease["lease_epoch"]),
            process_instance_id=str(lease["process_instance_id"]),
        )

    def release_execution_lease(self, lease: dict[str, Any]) -> bool:
        from rexecop.runtime_ops.lease import WorkerLeaseManager

        return WorkerLeaseManager(self.root).release(
            owner_token=str(lease["owner_token"]),
            lease_epoch=int(lease["lease_epoch"]),
            process_instance_id=str(lease["process_instance_id"]),
        )

    def validate_execution_lease(self, lease: dict[str, Any]) -> None:
        from rexecop.runtime_ops.lease import WorkerLeaseManager

        WorkerLeaseManager(self.root).validate(lease)

    def _queue_claim_lifecycle(self) -> Any:
        from rexecop.runtime_ops.queue import RunNowQueue

        return RunNowQueue(self)

    def queue_list_pending(self) -> list[str]:
        return self._queue_claim_lifecycle().list_pending()

    def queue_position(self, operation_id: str) -> int | None:
        return self._queue_claim_lifecycle().position(operation_id)

    def queue_enqueue(self, operation_id: str) -> int:
        return self._queue_claim_lifecycle().enqueue(operation_id)

    def queue_remove(self, operation_id: str) -> None:
        self._queue_claim_lifecycle().remove(operation_id)

    def queue_discard_pending(self, operation_id: str) -> None:
        self._queue_claim_lifecycle().discard_pending(operation_id)

    def queue_claim(self, lease: dict[str, Any]) -> dict[str, Any] | None:
        return self._queue_claim_lifecycle().claim_from_lease(lease)

    def queue_complete_claim(self, operation_id: str, lease: dict[str, Any]) -> None:
        self._queue_claim_lifecycle().complete_claim_from_lease(operation_id, lease)

    def start_execution_attempt(self, **binding: Any) -> dict[str, Any]:
        from rexecop.runtime_ops.attempts import AttemptJournal

        return AttemptJournal(self.root).start(**binding)

    def allocate_execution_attempt_id(self) -> str:
        from rexecop.runtime_ops.attempts import AttemptJournal

        return AttemptJournal.allocate_id()

    def finish_execution_attempt(
        self,
        attempt: dict[str, Any],
        *,
        status: str,
        result_digest: str = "",
        error_class: str = "",
    ) -> dict[str, Any]:
        from rexecop.runtime_ops.attempts import AttemptJournal

        return AttemptJournal(self.root).finish(
            attempt,
            status=status,
            result_digest=result_digest,
            error_class=error_class,
        )

    def finish_indeterminate_if_started(
        self,
        attempt: dict[str, Any],
        *,
        result_digest: str = "",
    ) -> dict[str, Any]:
        from rexecop.runtime_ops.attempts import AttemptJournal

        return AttemptJournal(self.root).finish_indeterminate_if_started(
            attempt,
            result_digest=result_digest,
        )

    def recover_started_attempts(self) -> list[str]:
        from rexecop.runtime_ops.attempts import AttemptJournal

        return AttemptJournal(self.root).mark_started_indeterminate()

    def has_indeterminate_side_effect(self, operation_id: str) -> bool:
        from rexecop.runtime_ops.attempts import AttemptJournal

        return AttemptJournal(self.root).has_indeterminate_side_effect(operation_id)

    def list_execution_attempts(self, operation_id: str) -> list[dict[str, Any]]:
        from rexecop.runtime_ops.attempts import AttemptJournal

        return AttemptJournal(self.root).list(operation_id)

    def _queue_claim_facts(
        self,
        operation_id: str,
    ) -> tuple[Operation, list[dict[str, Any]]]:
        return self.load_operation(operation_id), self.list_execution_attempts(operation_id)

    def list_pending_projection_operations(self) -> list[Operation]:
        return [
            operation
            for operation in self.list_operations()
            if isinstance(operation.metadata.get("sclite_projection"), dict)
            and operation.metadata["sclite_projection"].get("status") == "pending"
        ]

    @contextmanager
    def _execution_permit_guard(self, operation_dir: Path) -> Iterator[None]:
        guard_path = operation_dir / ".attempt-permit.guard"
        with guard_path.open("a+", encoding="utf-8") as guard_file:
            secure_file(guard_path)
            fcntl.flock(guard_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(guard_file.fileno(), fcntl.LOCK_UN)

    def save_execution_permit(self, permit: dict[str, Any]) -> Path:
        self.ensure_layout()
        operation_id = str(permit["operation_id"])
        step_id = str(permit["step_id"])
        attempt_id = str(permit["attempt_id"])
        operation_dir = self.permits_dir / operation_id
        secure_directory(operation_dir)
        attempts_dir = operation_dir / "attempts"
        secure_directory(attempts_dir)
        attempt_path = attempts_dir / f"{attempt_id}.json"
        with self._execution_permit_guard(operation_dir):
            if attempt_path.exists():
                raise RExecOpValidationError("runtime attempt permit already exists")
            self._write_json(attempt_path, permit)
            path = operation_dir / f"{step_id}.json"
            self._write_json(path, permit)
        return attempt_path

    def load_execution_permit(self, operation_id: str, step_id: str) -> dict[str, Any]:
        path = self.permits_dir / operation_id / f"{step_id}.json"
        if not path.is_file():
            raise RExecOpValidationError(f"execution permit not found: {operation_id}/{step_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def load_execution_permit_for_attempt(
        self,
        operation_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        path = self.permits_dir / operation_id / "attempts" / f"{attempt_id}.json"
        if not path.is_file():
            raise RExecOpValidationError(
                f"runtime attempt permit not found: {operation_id}/{attempt_id}"
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def claim_governance_decision_once(
        self,
        *,
        decision_digest: str,
        nonce: str,
        attempt_id: str,
        runtime_instance_id: str,
    ) -> bool:
        """Atomically reject reuse of either a decision digest or its nonce."""
        from hashlib import sha256

        digest_value = decision_digest.removeprefix("sha256:")
        if (
            len(digest_value) != 64
            or any(char not in "0123456789abcdef" for char in digest_value)
            or not nonce.strip()
            or len(nonce) > 256
            or not attempt_id.startswith("attempt-")
            or len(attempt_id) > 96
            or not runtime_instance_id.strip()
            or len(runtime_instance_id) > 256
        ):
            raise RExecOpValidationError("invalid governance decision claim")
        self.ensure_layout()
        digest_key = sha256(decision_digest.encode("utf-8")).hexdigest()
        nonce_key = sha256(nonce.encode("utf-8")).hexdigest()
        digest_path = self.governance_claims_dir / f"decision-{digest_key}.json"
        nonce_path = self.governance_claims_dir / f"nonce-{nonce_key}.json"
        lock_path = self.governance_claims_dir / ".claim.lock"
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if digest_path.exists() or nonce_path.exists():
                return False
            record = {
                "schema": "rexecop.governance_decision_claim.v0.1",
                "decision_digest": decision_digest,
                "nonce_digest": f"sha256:{nonce_key}",
                "attempt_id": attempt_id,
                "runtime_instance_id": runtime_instance_id,
            }
            self._write_json(digest_path, record)
            self._write_json(
                nonce_path,
                {
                    "schema": "rexecop.governance_nonce_claim.v0.1",
                    "decision_claim_ref": f"sha256:{digest_key}",
                    "attempt_id": attempt_id,
                    "runtime_instance_id": runtime_instance_id,
                },
            )
            return True
