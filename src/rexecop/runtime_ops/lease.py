from __future__ import annotations

import fcntl
import json
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from rexecop.errors import RExecOpLeaseLost, RExecOpValidationError
from rexecop.storage.atomic import atomic_write_text, secure_directory, secure_file

WORKER_LEASE_SCHEMA = "rexecop.worker_lease.v0.2"
DEFAULT_LEASE_TTL_SECONDS = 120.0


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class WorkerLeaseManager:
    """Process-safe, fenced single-executor lease for a local runtime root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.lease_path = root / "watchdog" / "worker_lease.json"
        self.lock_path = root / "watchdog" / "worker_lease.lock"
        self.epoch_path = root / "watchdog" / "worker_lease.epoch"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        secure_directory(self.lease_path.parent)
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def read(self) -> dict[str, Any] | None:
        with self._locked():
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any] | None:
        if not self.lease_path.is_file():
            return None
        secure_file(self.lease_path)
        payload = json.loads(self.lease_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None

    def is_stale(
        self,
        lease: Mapping[str, Any] | None = None,
        *,
        now: datetime | None = None,
        max_age_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> bool:
        record = dict(lease) if lease is not None else self.read()
        if not record or str(record.get("schema") or "") != WORKER_LEASE_SCHEMA:
            return True
        expiry = _parse_time(str(record.get("expires_at") or ""))
        if expiry is None:
            return True
        return (now or _utc_now()) >= expiry

    def clear_if_stale(
        self,
        *,
        now: datetime | None = None,
        max_age_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> bool:
        with self._locked():
            existing = self._read_unlocked()
            if not existing or not self.is_stale(
                existing, now=now, max_age_seconds=max_age_seconds
            ):
                return False
            self.lease_path.unlink(missing_ok=True)
            return True

    def acquire(
        self,
        *,
        worker_id: str,
        process_instance_id: str | None = None,
        now: datetime | None = None,
        max_age_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> dict[str, Any]:
        if not worker_id.strip():
            raise RExecOpValidationError("worker_id must not be empty")
        observed_at = now or _utc_now()
        with self._locked():
            existing = self._read_unlocked()
            if existing and not self.is_stale(
                existing, now=observed_at, max_age_seconds=max_age_seconds
            ):
                raise RExecOpLeaseLost(
                    f"worker lease held by {str(existing.get('worker_id') or '')!r}"
                )
            persisted_epoch = 0
            if self.epoch_path.is_file():
                secure_file(self.epoch_path)
                try:
                    persisted_epoch = int(self.epoch_path.read_text(encoding="utf-8").strip())
                except ValueError:
                    persisted_epoch = 0
            previous_epoch = max(persisted_epoch, int((existing or {}).get("lease_epoch") or 0))
            record = {
                "schema": WORKER_LEASE_SCHEMA,
                "worker_id": worker_id,
                "process_instance_id": process_instance_id or str(uuid.uuid4()),
                "lease_epoch": previous_epoch + 1,
                "owner_token": uuid.uuid4().hex,
                "acquired_at": observed_at.isoformat(),
                "heartbeat_at": observed_at.isoformat(),
                "expires_at": (observed_at + timedelta(seconds=max_age_seconds)).isoformat(),
                "monotonic_seconds": round(time.monotonic(), 6),
            }
            atomic_write_text(self.epoch_path, f"{record['lease_epoch']}\n")
            self._write_unlocked(record)
            return record

    def renew(
        self,
        *,
        owner_token: str,
        lease_epoch: int,
        process_instance_id: str,
        now: datetime | None = None,
        monotonic_seconds: float | None = None,
        max_age_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> dict[str, Any]:
        observed_at = now or _utc_now()
        with self._locked():
            existing = self._require_owner(owner_token, lease_epoch, process_instance_id)
            monotonic = float(
                monotonic_seconds if monotonic_seconds is not None else time.monotonic()
            )
            if monotonic < float(existing.get("monotonic_seconds") or 0):
                raise RExecOpValidationError("worker lease monotonic clock moved backwards")
            existing.update(
                heartbeat_at=observed_at.isoformat(),
                expires_at=(observed_at + timedelta(seconds=max_age_seconds)).isoformat(),
                monotonic_seconds=round(monotonic, 6),
            )
            self._write_unlocked(existing)
            return existing

    def release(self, *, owner_token: str, lease_epoch: int, process_instance_id: str) -> bool:
        with self._locked():
            self._require_owner(owner_token, lease_epoch, process_instance_id)
            self.lease_path.unlink(missing_ok=True)
            return True

    def validate(self, lease: dict[str, Any], *, now: datetime | None = None) -> None:
        with self._locked():
            existing = self._require_owner(
                str(lease.get("owner_token") or ""),
                int(lease.get("lease_epoch") or 0),
                str(lease.get("process_instance_id") or ""),
            )
            if self.is_stale(existing, now=now):
                raise RExecOpLeaseLost("execution lease expired")

    def _require_owner(
        self, owner_token: str, lease_epoch: int, process_instance_id: str
    ) -> dict[str, Any]:
        existing = self._read_unlocked()
        if not existing or (
            str(existing.get("owner_token") or "") != owner_token
            or int(existing.get("lease_epoch") or 0) != lease_epoch
            or str(existing.get("process_instance_id") or "") != process_instance_id
        ):
            raise RExecOpLeaseLost("worker lease ownership conflict")
        return existing

    def _write_unlocked(self, record: dict[str, Any]) -> None:
        atomic_write_text(self.lease_path, json.dumps(record, indent=2, sort_keys=True) + "\n")


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
