from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rexecop.catalog.digest import canonical_digest
from rexecop.errors import RExecOpValidationError
from rexecop.storage.atomic import atomic_write_text, secure_directory, secure_file

ATTEMPT_SCHEMA = "rexecop.execution_attempt.v0.1"
ATTEMPT_STATUSES = frozenset({"pending", "started", "completed", "failed", "indeterminate"})


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def build_pending_attempt(
    *,
    operation_id: str,
    attempt_id: str,
    operation_revision: int,
    step_id: str,
    plan: dict[str, Any],
    execution_spec: dict[str, Any] | None,
    target: str,
    mode: str,
    lease: dict[str, Any],
    execution_permit_ref: str = "",
) -> dict[str, Any]:
    if not attempt_id.startswith("attempt-") or len(attempt_id) > 96:
        raise RExecOpValidationError("invalid execution attempt id")
    return {
        "schema": ATTEMPT_SCHEMA,
        "attempt_id": attempt_id,
        "operation_id": operation_id,
        "operation_revision": operation_revision,
        "step_id": step_id,
        "status": "pending",
        "plan_digest": "sha256:" + canonical_digest(plan),
        "execution_spec_digest": str((execution_spec or {}).get("digest") or ""),
        "target": target,
        "mode": mode,
        "side_effectful": mode not in {"observe", "dry_run", "emergency_readonly"},
        "execution_permit_ref": execution_permit_ref,
        "lease_epoch": int(lease.get("lease_epoch") or 0),
        "process_instance_id": str(lease.get("process_instance_id") or ""),
        "created_at": _now(),
    }


def mark_attempt_started(record: dict[str, Any]) -> dict[str, Any]:
    started = dict(record)
    started["status"] = "started"
    started["started_at"] = _now()
    return started


def finish_attempt_record(
    record: dict[str, Any],
    *,
    status: str,
    result_digest: str = "",
    error_class: str = "",
) -> dict[str, Any]:
    if status not in {"completed", "failed", "indeterminate"}:
        raise RExecOpValidationError(f"invalid attempt terminal status: {status}")
    if record.get("status") != "started":
        raise RExecOpValidationError("execution attempt is not started")
    finished = dict(record)
    finished.update(
        status=status,
        finished_at=_now(),
        result_digest=result_digest,
        error_class=error_class,
    )
    return finished


def finish_attempt_indeterminate_if_started(
    record: dict[str, Any],
    *,
    result_digest: str = "",
) -> dict[str, Any]:
    status = str(record.get("status") or "")
    if status in {"completed", "failed", "indeterminate"}:
        return dict(record)
    if status != "started":
        raise RExecOpValidationError("execution attempt is not started or terminal")
    return finish_attempt_record(
        record,
        status="indeterminate",
        result_digest=result_digest,
        error_class="outcome_indeterminate",
    )


class AttemptJournal:
    """Durable connector-attempt lifecycle; a started record means IO may occur."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.attempts_dir = root / "attempts"

    @staticmethod
    def allocate_id() -> str:
        """Allocate an attempt identity before governance is requested."""
        return f"attempt-{uuid.uuid4().hex}"

    def start(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        operation_revision: int,
        step_id: str,
        plan: dict[str, Any],
        execution_spec: dict[str, Any] | None,
        target: str,
        mode: str,
        lease: dict[str, Any],
        execution_permit_ref: str = "",
    ) -> dict[str, Any]:
        operation_dir = self.attempts_dir / operation_id
        secure_directory(operation_dir)
        path = operation_dir / f"{attempt_id}.json"
        if path.exists():
            raise RExecOpValidationError("execution attempt already exists")
        record = build_pending_attempt(
            operation_id=operation_id,
            attempt_id=attempt_id,
            operation_revision=operation_revision,
            step_id=step_id,
            plan=plan,
            execution_spec=execution_spec,
            target=target,
            mode=mode,
            lease=lease,
            execution_permit_ref=execution_permit_ref,
        )
        self._write(path, record)
        record = mark_attempt_started(record)
        self._write(path, record)
        return record

    def finish(
        self,
        record: dict[str, Any],
        *,
        status: str,
        result_digest: str = "",
        error_class: str = "",
    ) -> dict[str, Any]:
        path = self._path(record)
        current = self._read(path)
        current = finish_attempt_record(
            current,
            status=status,
            result_digest=result_digest,
            error_class=error_class,
        )
        self._write(path, current)
        return current

    def finish_indeterminate_if_started(
        self,
        record: dict[str, Any],
        *,
        result_digest: str = "",
    ) -> dict[str, Any]:
        path = self._path(record)
        current = self._read(path)
        current = finish_attempt_indeterminate_if_started(
            current,
            result_digest=result_digest,
        )
        self._write(path, current)
        return current

    def mark_started_indeterminate(self) -> list[str]:
        changed: list[str] = []
        if not self.attempts_dir.is_dir():
            return changed
        for path in sorted(self.attempts_dir.glob("*/*.json")):
            record = self._read(path)
            if record.get("status") != "started":
                continue
            record.update(
                status="indeterminate",
                finished_at=_now(),
                error_class="outcome_indeterminate",
            )
            self._write(path, record)
            changed.append(str(record.get("attempt_id") or path.stem))
        return changed

    def has_indeterminate_side_effect(self, operation_id: str) -> bool:
        operation_dir = self.attempts_dir / operation_id
        if not operation_dir.is_dir():
            return False
        return any(
            record.get("status") == "indeterminate" and record.get("side_effectful") is True
            for record in (self._read(path) for path in operation_dir.glob("*.json"))
        )

    def list(self, operation_id: str) -> list[dict[str, Any]]:
        operation_dir = self.attempts_dir / operation_id
        if not operation_dir.is_dir():
            return []
        return [self._read(path) for path in sorted(operation_dir.glob("*.json"))]

    def _path(self, record: dict[str, Any]) -> Path:
        return self.attempts_dir / str(record["operation_id"]) / f"{str(record['attempt_id'])}.json"

    def _read(self, path: Path) -> dict[str, Any]:
        secure_file(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RExecOpValidationError(f"invalid execution attempt: {path.name}")
        return payload

    def _write(self, path: Path, record: dict[str, Any]) -> None:
        atomic_write_text(path, json.dumps(record, indent=2, sort_keys=True) + "\n")
