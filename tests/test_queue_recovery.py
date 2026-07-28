from __future__ import annotations

import fcntl
import hashlib
import json
import multiprocessing
import traceback
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Event, Thread
from typing import Any

import pytest

from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
from rexecop.connectors.static_fixture import StaticFixtureRuntime
from rexecop.errors import RExecOpConcurrencyConflict, RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.operation.model import Operation
from rexecop.runtime_ops.queue import (
    INVALID_QUEUE_CLAIM_PARAMETERS,
    QUEUE_CLAIM_LIFECYCLE_UNSUPPORTED,
    QUEUE_CLAIM_RECOVERY_BLOCKED,
    RunNowQueue,
    StoreRunNowQueue,
)
from rexecop.runtime_ops.recovery import run_startup_recovery
from rexecop.storage.file_store import FileStore
from rexecop.storage.memory_store import InMemoryStore
from rexecop.storage.sqlite_store import SqliteStore
from runtime_governance_support import governance_runtime_kwargs

pytestmark = pytest.mark.m9_runtime

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"
OBSERVED_AT = datetime.now(UTC).replace(microsecond=0)
Store = FileStore | InMemoryStore | SqliteStore


class _UnsupportedFileStore(FileStore):
    pass


def _recover_claims(
    store: Store,
    lease: dict[str, Any],
    *,
    observed_at: datetime | None = None,
) -> bool:
    return StoreRunNowQueue(store).recover_expired_claims_from_lease(
        lease,
        observed_at=observed_at,
    )


def _operation(operation_id: str, *, state: str = "approved") -> Operation:
    return Operation(
        id=operation_id,
        profile="fixture",
        environment="fixture",
        intent="inspect",
        target="target",
        mode="dry_run",
        requested_by="test",
        state=state,
        created_at="2026-07-27T00:00:00+00:00",
        updated_at="2026-07-27T00:00:00+00:00",
    )


def _store(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Store:
    if kind == "file":
        return FileStore(tmp_path / "file")
    if kind == "sqlite":
        return SqliteStore(tmp_path / "sqlite")
    memory_cwd = tmp_path / "memory"
    memory_cwd.mkdir()
    monkeypatch.chdir(memory_cwd)
    return InMemoryStore()


def _queue_payload(store: Store) -> dict[str, Any]:
    return json.loads((store.root / "queue" / "run_now.json").read_text(encoding="utf-8"))


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _expire_claim_and_worker(store: Store, operation_id: str) -> None:
    queue_payload = _queue_payload(store)
    queue_payload["claims"][operation_id]["expires_at"] = "2000-01-01T00:00:00+00:00"
    _write_payload(store.root / "queue" / "run_now.json", queue_payload)
    lease_path = store.root / "watchdog" / "worker_lease.json"
    lease_payload = json.loads(lease_path.read_text(encoding="utf-8"))
    lease_payload["expires_at"] = "2000-01-01T00:00:00+00:00"
    _write_payload(lease_path, lease_payload)


def _set_current_lease_process_identity(
    store: Store,
    lease: dict[str, Any],
    process_instance_id: str,
) -> None:
    lease_path = store.root / "watchdog" / "worker_lease.json"
    lease_payload = json.loads(lease_path.read_text(encoding="utf-8"))
    lease_payload["process_instance_id"] = process_instance_id
    _write_payload(lease_path, lease_payload)
    lease["process_instance_id"] = process_instance_id


def _claimed_expired_operation(store: Store, operation: Operation) -> dict[str, Any]:
    store.save_operation(operation)
    store.queue_enqueue(operation.id)
    lease = store.acquire_execution_lease(worker_id="crashed-worker")
    claim = store.queue_claim(lease)
    assert claim is not None
    assert claim["operation_id"] == operation.id
    _expire_claim_and_worker(store, operation.id)
    return lease


def _start_attempt(store: Store, operation_id: str, lease: dict[str, Any]) -> dict[str, Any]:
    return store.start_execution_attempt(
        operation_id=operation_id,
        attempt_id=store.allocate_execution_attempt_id(),
        operation_revision=1,
        step_id="fixture-step",
        plan={"operation_id": operation_id},
        execution_spec={"digest": "sha256:" + "a" * 64},
        target="fixture-target",
        mode="apply",
        lease=lease,
    )


@pytest.mark.parametrize("kind", ["file", "memory", "sqlite"])
def test_expired_pre_execution_claim_requeues_once_in_logical_store(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(kind, tmp_path, monkeypatch)
    operation = _operation(f"op-safe-{kind}")
    old_lease = _claimed_expired_operation(store, operation)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")

    assert _recover_claims(
        store,
        recovery_lease,
        observed_at=OBSERVED_AT,
    )
    payload = _queue_payload(store)
    assert payload["pending"] == [operation.id]
    recovered = payload["claims"][operation.id]
    assert recovered["status"] == "requeued"
    assert recovered["attempt"] == 1
    assert recovered["last_transition"] == {
        "attempt_count": 0,
        "attempt_status_counts": {
            "completed": 0,
            "failed": 0,
            "indeterminate": 0,
            "pending": 0,
            "started": 0,
        },
        "claim_attempt": 1,
        "current_lease_epoch": recovery_lease["lease_epoch"],
        "current_process_instance_id": recovery_lease["process_instance_id"],
        "disposition": "requeued",
        "operation_state": "approved",
        "prior_lease_epoch": old_lease["lease_epoch"],
        "prior_process_instance_id": old_lease["process_instance_id"],
        "private_schema": "rexecop.queue_claim_transition.v0.1",
        "reason": "expired_pre_execution_claim",
        "recorded_at": OBSERVED_AT.isoformat(),
    }

    snapshot = (store.root / "queue" / "run_now.json").read_bytes()
    assert not _recover_claims(
        store,
        recovery_lease,
        observed_at=OBSERVED_AT,
    )
    assert (store.root / "queue" / "run_now.json").read_bytes() == snapshot
    with pytest.raises(RExecOpConcurrencyConflict):
        store.queue_complete_claim(operation.id, old_lease)

    next_claim = store.queue_claim(recovery_lease)
    assert next_claim is not None
    assert next_claim["attempt"] == 2
    assert next_claim["last_transition"] == recovered["last_transition"]
    with pytest.raises(RExecOpConcurrencyConflict):
        store.queue_complete_claim(operation.id, old_lease)
    store.queue_complete_claim(operation.id, recovery_lease)
    completed_snapshot = (store.root / "queue" / "run_now.json").read_bytes()
    assert not _recover_claims(
        store,
        recovery_lease,
        observed_at=OBSERVED_AT,
    )
    assert (store.root / "queue" / "run_now.json").read_bytes() == completed_snapshot


def test_fresh_claim_is_unchanged_and_same_or_lower_epoch_cannot_reclaim(
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-fencing")
    store.save_operation(operation)
    store.queue_enqueue(operation.id)
    first_lease = store.acquire_execution_lease(worker_id="first-worker")
    assert store.queue_claim(first_lease) is not None
    fresh_snapshot = (store.root / "queue" / "run_now.json").read_bytes()

    assert not _recover_claims(store, first_lease)
    assert (store.root / "queue" / "run_now.json").read_bytes() == fresh_snapshot

    _expire_claim_and_worker(store, operation.id)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")
    for claim_epoch in (
        recovery_lease["lease_epoch"],
        recovery_lease["lease_epoch"] + 1,
    ):
        payload = _queue_payload(store)
        payload["claims"][operation.id]["lease_epoch"] = claim_epoch
        payload["claims"][operation.id]["expires_at"] = "2000-01-01T00:00:00+00:00"
        _write_payload(store.root / "queue" / "run_now.json", payload)
        with pytest.raises(RExecOpValidationError) as caught:
            _recover_claims(store, recovery_lease, observed_at=OBSERVED_AT)
        assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
        assert _queue_payload(store)["pending"] == []


def test_expired_legacy_pending_claim_normalizes_without_duplicate(
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-safe-legacy")
    old_lease = _claimed_expired_operation(store, operation)
    with pytest.raises(RExecOpConcurrencyConflict):
        store.queue_enqueue(operation.id)
    payload = _queue_payload(store)
    payload["pending"] = [operation.id]
    _write_payload(store.root / "queue" / "run_now.json", payload)
    recovery_lease = store.acquire_execution_lease(worker_id="legacy-recovery")

    assert _recover_claims(store, recovery_lease, observed_at=OBSERVED_AT)
    normalized = _queue_payload(store)
    assert normalized["pending"] == [operation.id]
    claim = normalized["claims"][operation.id]
    assert claim["status"] == "requeued"
    assert claim["last_transition"]["reason"] == "expired_pre_execution_claim"
    assert claim["last_transition"]["prior_lease_epoch"] == old_lease["lease_epoch"]
    next_claim = store.queue_claim(recovery_lease)
    assert next_claim is not None
    assert next_claim["attempt"] == 2


@pytest.mark.parametrize(
    "relation",
    [
        "prior_process_is_prior_owner",
        "prior_process_is_current_owner",
        "current_process_is_prior_owner",
        "current_process_is_current_owner",
        "nonalias",
    ],
)
def test_transition_process_identity_redacts_exact_owner_token_aliases(
    relation: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation(f"op-process-alias-{relation}")
    _claimed_expired_operation(store, operation)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")
    payload = _queue_payload(store)
    prior_owner_token = str(payload["claims"][operation.id]["owner_token"])
    current_owner_token = str(recovery_lease["owner_token"])
    prior_process = "prior-process-nonalias"
    current_process = "current-process-nonalias"
    if relation == "prior_process_is_prior_owner":
        prior_process = prior_owner_token
    elif relation == "prior_process_is_current_owner":
        prior_process = current_owner_token
    elif relation == "current_process_is_prior_owner":
        current_process = prior_owner_token
    elif relation == "current_process_is_current_owner":
        current_process = current_owner_token
    payload["claims"][operation.id]["process_instance_id"] = prior_process
    _write_payload(store.root / "queue" / "run_now.json", payload)
    _set_current_lease_process_identity(store, recovery_lease, current_process)

    assert _recover_claims(store, recovery_lease, observed_at=OBSERVED_AT)

    transition = _queue_payload(store)["claims"][operation.id]["last_transition"]
    marker = next(
        candidate
        for candidate in (
            "redacted-process-identity",
            "redacted-process-identity-2",
            "redacted-process-identity-3",
        )
        if candidate not in {prior_owner_token, current_owner_token}
    )
    expected_prior = (
        marker
        if prior_process in {prior_owner_token, current_owner_token}
        else prior_process
    )
    expected_current = (
        marker
        if current_process in {prior_owner_token, current_owner_token}
        else current_process
    )
    assert transition["prior_process_instance_id"] == expected_prior
    assert transition["current_process_instance_id"] == expected_current
    assert len(expected_prior) <= 128
    assert len(expected_current) <= 128
    assert set(transition) == {
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
    serialized = json.dumps(transition, sort_keys=True)
    for token in (prior_owner_token, current_owner_token):
        assert token not in serialized
        assert hashlib.sha256(token.encode("utf-8")).hexdigest() not in serialized


@pytest.mark.parametrize(
    "case",
    [
        "fresh",
        "same_epoch",
        "lower_epoch",
        "attempted",
        "wrong_state",
        "duplicate",
        "malformed",
    ],
)
def test_unsafe_legacy_pending_claim_never_normalizes(
    case: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation(
        f"op-unsafe-legacy-{case}",
        state="planned" if case == "wrong_state" else "approved",
    )
    store.save_operation(operation)
    store.queue_enqueue(operation.id)
    old_lease = store.acquire_execution_lease(worker_id="legacy-owner")
    claim = store.queue_claim(old_lease)
    assert claim is not None
    if case == "attempted":
        _start_attempt(store, operation.id, old_lease)
    _expire_claim_and_worker(store, operation.id)
    recovery_lease = store.acquire_execution_lease(worker_id="legacy-recovery")
    payload = _queue_payload(store)
    payload["pending"] = (
        [operation.id, operation.id] if case == "duplicate" else [operation.id]
    )
    if case == "fresh":
        payload["claims"][operation.id]["expires_at"] = "2999-01-01T00:00:00+00:00"
    elif case == "same_epoch":
        payload["claims"][operation.id]["lease_epoch"] = recovery_lease["lease_epoch"]
    elif case == "lower_epoch":
        payload["claims"][operation.id]["lease_epoch"] = (
            recovery_lease["lease_epoch"] + 1
        )
    elif case == "malformed":
        payload["claims"][operation.id]["attempt"] = 0
    _write_payload(store.root / "queue" / "run_now.json", payload)

    with pytest.raises(
        RExecOpValidationError,
        match=f"^{QUEUE_CLAIM_RECOVERY_BLOCKED}$",
    ):
        _recover_claims(store, recovery_lease, observed_at=OBSERVED_AT)

    blocked = _queue_payload(store)
    assert blocked["claims"][operation.id]["status"] == "claimed"
    assert blocked["pending"] == payload["pending"]


def test_repeated_exact_defer_is_byte_identical_and_fenced_from_later_attempt(
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-exact-defer")
    store.save_operation(operation)
    store.queue_enqueue(operation.id)
    lease = store.acquire_execution_lease(worker_id="defer-worker")
    _set_current_lease_process_identity(store, lease, str(lease["owner_token"]))
    first_claim = store.queue_claim(lease)
    assert first_claim is not None
    queue = StoreRunNowQueue(store)

    queue.defer_claim_from_lease(
        operation.id,
        first_claim,
        lease,
        reason="max_concurrent_reached",
    )
    snapshot = (store.root / "queue" / "run_now.json").read_bytes()
    transition = _queue_payload(store)["claims"][operation.id]["last_transition"]
    serialized_transition = json.dumps(transition, sort_keys=True)
    assert "owner_token" not in serialized_transition
    assert lease["owner_token"] not in serialized_transition
    assert transition["prior_process_instance_id"] == "redacted-process-identity"
    assert transition["current_process_instance_id"] == "redacted-process-identity"
    queue.defer_claim_from_lease(
        operation.id,
        first_claim,
        lease,
        reason="max_concurrent_reached",
    )
    assert (store.root / "queue" / "run_now.json").read_bytes() == snapshot
    assert _queue_payload(store)["pending"] == [operation.id]

    later_claim = store.queue_claim(lease)
    assert later_claim is not None
    assert later_claim["attempt"] == first_claim["attempt"] + 1
    with pytest.raises(RExecOpConcurrencyConflict):
        queue.defer_claim_from_lease(
            operation.id,
            first_claim,
            lease,
            reason="max_concurrent_reached",
        )
    with pytest.raises(RExecOpConcurrencyConflict):
        queue.complete_claim_from_lease(
            operation.id,
            lease,
            claim_snapshot=first_claim,
        )
    queue.complete_claim_from_lease(
        operation.id,
        lease,
        claim_snapshot=later_claim,
    )


@pytest.mark.parametrize("kind", ["file", "memory", "sqlite"])
def test_builtin_store_uses_one_claim_lifecycle_for_full_sequence(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(kind, tmp_path, monkeypatch)
    operation = _operation(f"op-lifecycle-{kind}")
    store.save_operation(operation)
    assert store.queue_enqueue(operation.id) == 0
    lease = store.acquire_execution_lease(worker_id=f"lifecycle-{kind}")
    first_claim = store.queue_claim(lease)
    assert first_claim is not None
    lifecycle = StoreRunNowQueue(store)
    lifecycle.defer_claim_from_lease(
        operation.id,
        first_claim,
        lease,
        reason="max_concurrent_reached",
    )
    later_claim = store.queue_claim(lease)
    assert later_claim is not None
    assert later_claim["attempt"] == first_claim["attempt"] + 1
    lifecycle.complete_claim_from_lease(
        operation.id,
        lease,
        claim_snapshot=later_claim,
    )

    payload = _queue_payload(store)
    assert payload["pending"] == []
    assert payload["claims"][operation.id]["status"] == "completed"
    assert payload["claims"][operation.id]["attempt"] == 2


def test_invalid_public_claim_parameters_never_mutate_or_create_queue(
    tmp_path: Path,
) -> None:
    invalid: list[dict[str, Any]] = [
        {"owner_token": ""},
        {"owner_token": "owner\ncontrol"},
        {"owner_token": "x" * 129},
        {"process_instance_id": ""},
        {"process_instance_id": "process\u200bcontrol"},
        {"process_instance_id": "x" * 129},
        {"lease_epoch": False},
        {"lease_epoch": 0},
        {"lease_epoch": 1 << 63},
        {"ttl_seconds": False},
        {"ttl_seconds": 0.0},
        {"ttl_seconds": -1.0},
        {"ttl_seconds": float("nan")},
        {"ttl_seconds": float("inf")},
        {"ttl_seconds": 1e308},
        {"ttl_seconds": 10**10_000},
        {"ttl_seconds": 5e-324},
        {"ttl_seconds": "120"},
    ]
    valid: dict[str, Any] = {
        "owner_token": "owner",
        "lease_epoch": 1,
        "process_instance_id": "process",
        "ttl_seconds": 120.0,
    }
    for index, override in enumerate(invalid):
        store = FileStore(tmp_path / f"fresh-{index}")
        queue = RunNowQueue(store)
        parameters = dict(valid, **override)
        with pytest.raises(
            RExecOpValidationError,
            match=f"^{INVALID_QUEUE_CLAIM_PARAMETERS}$",
        ) as caught:
            queue.claim(**parameters)
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__ is True
        assert not queue.queue_dir.exists()

    existing_store = FileStore(tmp_path / "existing")
    existing_queue = RunNowQueue(existing_store)
    existing_queue.enqueue("op-existing")
    snapshot = existing_queue.queue_file.read_bytes()
    for override in invalid:
        parameters = dict(valid, **override)
        with pytest.raises(
            RExecOpValidationError,
            match=f"^{INVALID_QUEUE_CLAIM_PARAMETERS}$",
        ):
            existing_queue.claim(**parameters)
        assert existing_queue.queue_file.read_bytes() == snapshot


def test_private_claim_rejects_invalid_ttl_before_recovery_mutation(
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-private-invalid-ttl")
    _claimed_expired_operation(store, operation)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")
    snapshot = (store.root / "queue" / "run_now.json").read_bytes()

    with pytest.raises(
        RExecOpValidationError,
        match=f"^{INVALID_QUEUE_CLAIM_PARAMETERS}$",
    ):
        RunNowQueue(store).claim_from_lease(
            recovery_lease,
            ttl_seconds=float("nan"),
        )

    assert (store.root / "queue" / "run_now.json").read_bytes() == snapshot


def test_valid_public_claim_can_complete_and_reuse_operation_id(
    tmp_path: Path,
) -> None:
    queue = RunNowQueue(FileStore(tmp_path / ".rexecop"))
    assert queue.enqueue("op-public-reuse") == 0
    first = queue.claim(
        owner_token="fixture-owner-a",
        lease_epoch=1,
        process_instance_id="process-one",
    )
    assert first is not None
    queue.complete_claim(
        "op-public-reuse",
        owner_token="fixture-owner-a",
        lease_epoch=1,
    )
    assert queue.enqueue("op-public-reuse") == 0
    later = queue.claim(
        owner_token="fixture-owner-b",
        lease_epoch=2,
        process_instance_id="process-two",
    )
    assert later is not None
    assert later["attempt"] == first["attempt"] + 1
    queue.complete_claim(
        "op-public-reuse",
        owner_token="fixture-owner-b",
        lease_epoch=2,
    )


def test_concurrent_exact_defers_converge_to_one_pending_record(
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-concurrent-defer")
    store.save_operation(operation)
    store.queue_enqueue(operation.id)
    lease = store.acquire_execution_lease(worker_id="defer-worker")
    claim = store.queue_claim(lease)
    assert claim is not None
    barrier = Barrier(3)
    results: list[str] = []

    def defer() -> None:
        barrier.wait(timeout=10)
        StoreRunNowQueue(store).defer_claim_from_lease(
            operation.id,
            claim,
            lease,
            reason="target_locked",
        )
        results.append("ok")

    threads = [Thread(target=defer, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=10)
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert results == ["ok", "ok"]
    payload = _queue_payload(store)
    assert payload["pending"] == [operation.id]
    assert payload["claims"][operation.id]["status"] == "requeued"
    assert payload["claims"][operation.id]["last_transition"]["reason"] == (
        "target_locked"
    )


@pytest.mark.parametrize(
    ("corruption", "value"),
    [
        ("owner_token", "fixture-owner-token"),
        ("unknown_field", "fixture"),
        ("prior_process_instance_id", "x" * 129),
        ("current_process_instance_id", "x" * 10_000),
        ("attempt_count", "0"),
        ("attempt_count", 1),
        (
            "attempt_status_counts",
            {
                "completed": 0,
                "failed": 0,
                "indeterminate": 0,
                "pending": 1,
                "started": 0,
            },
        ),
        ("disposition", "unknown"),
    ],
)
def test_closed_last_transition_schema_rejects_before_dequeue_or_copy(
    corruption: str,
    value: Any,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-invalid-recovery-record")
    _claimed_expired_operation(store, operation)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")
    assert _recover_claims(store, recovery_lease, observed_at=OBSERVED_AT)
    payload = _queue_payload(store)
    payload["claims"][operation.id]["last_transition"][corruption] = value
    _write_payload(store.root / "queue" / "run_now.json", payload)
    snapshot = (store.root / "queue" / "run_now.json").read_bytes()

    with pytest.raises(RExecOpValidationError) as caught:
        store.queue_claim(recovery_lease)

    assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
    assert (store.root / "queue" / "run_now.json").read_bytes() == snapshot
    assert _queue_payload(store)["pending"] == [operation.id]


def test_pending_attempt_blocks_the_sole_approved_requeue_case(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-pending-attempt")
    old_lease = _claimed_expired_operation(store, operation)
    attempt = _start_attempt(store, operation.id, old_lease)
    attempt_path = (
        store.root / "attempts" / operation.id / f"{attempt['attempt_id']}.json"
    )
    payload = json.loads(attempt_path.read_text(encoding="utf-8"))
    payload["status"] = "pending"
    payload.pop("started_at", None)
    _write_payload(attempt_path, payload)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")

    with pytest.raises(RExecOpValidationError) as caught:
        _recover_claims(store, recovery_lease, observed_at=OBSERVED_AT)

    assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
    queue_payload = _queue_payload(store)
    assert queue_payload["pending"] == []
    assert queue_payload["claims"][operation.id]["last_transition"]["reason"] == (
        "approved_operation_has_attempts"
    )


@pytest.mark.parametrize("action", ["recover", "claim", "complete"])
def test_closed_last_transition_schema_applies_to_every_queue_transaction(
    action: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation(f"op-invalid-recovery-{action}")
    _claimed_expired_operation(store, operation)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")
    assert _recover_claims(store, recovery_lease, observed_at=OBSERVED_AT)
    if action == "complete":
        assert store.queue_claim(recovery_lease) is not None
    payload = _queue_payload(store)
    payload["claims"][operation.id]["last_transition"]["owner_token"] = "forbidden"
    _write_payload(store.root / "queue" / "run_now.json", payload)
    snapshot = (store.root / "queue" / "run_now.json").read_bytes()

    with pytest.raises(RExecOpValidationError) as caught:
        if action == "recover":
            _recover_claims(store, recovery_lease, observed_at=OBSERVED_AT)
        elif action == "claim":
            store.queue_claim(recovery_lease)
        else:
            store.queue_complete_claim(operation.id, recovery_lease)

    assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
    assert (store.root / "queue" / "run_now.json").read_bytes() == snapshot


@pytest.mark.parametrize("action", ["recover", "claim", "complete"])
def test_corrupt_current_lease_fails_at_guard_entry_without_queue_mutation(
    action: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation(f"op-corrupt-current-lease-{action}")
    if action == "recover":
        _claimed_expired_operation(store, operation)
        lease = store.acquire_execution_lease(worker_id="recovery-worker")
    else:
        store.save_operation(operation)
        store.queue_enqueue(operation.id)
        lease = store.acquire_execution_lease(worker_id="queue-worker")
        if action == "complete":
            assert store.queue_claim(lease) is not None
    queue_snapshot = (store.root / "queue" / "run_now.json").read_bytes()
    leak_marker = "fixture-owner-token-must-not-leak"
    lease_path = store.root / "watchdog" / "worker_lease.json"
    lease_path.write_text(
        '{"owner_token":"' + leak_marker + '", broken\n',
        encoding="utf-8",
    )

    expected_error = RExecOpConcurrencyConflict if action == "complete" else RExecOpValidationError
    with pytest.raises(expected_error) as caught:
        if action == "recover":
            _recover_claims(store, lease, observed_at=OBSERVED_AT)
        elif action == "claim":
            store.queue_claim(lease)
        else:
            store.queue_complete_claim(operation.id, lease)

    if action == "complete":
        assert str(caught.value) == (
            "concurrency_conflict: queue claim ownership lost " f"for {operation.id}"
        )
    else:
        assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    rendered = str(caught.value)
    assert leak_marker not in rendered
    assert "worker_lease.json" not in rendered
    assert "Expecting" not in rendered
    assert (store.root / "queue" / "run_now.json").read_bytes() == queue_snapshot


def test_queue_body_exception_is_not_translated_by_guard_entry_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-body-sentinel")
    _claimed_expired_operation(store, operation)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")
    snapshot = (store.root / "queue" / "run_now.json").read_bytes()
    sentinel = RuntimeError("queue-body-sentinel")

    def raise_body_sentinel(
        _queue: RunNowQueue,
        _operation_id: str,
    ) -> tuple[str, list[dict[str, Any]], str]:
        raise sentinel

    monkeypatch.setattr(RunNowQueue, "_load_recovery_facts", raise_body_sentinel)

    with pytest.raises(RuntimeError, match="^queue-body-sentinel$") as caught:
        _recover_claims(store, recovery_lease, observed_at=OBSERVED_AT)

    assert caught.value is sentinel
    assert (store.root / "queue" / "run_now.json").read_bytes() == snapshot
    renewed = store.renew_execution_lease(recovery_lease)
    assert renewed["lease_epoch"] == recovery_lease["lease_epoch"]


def test_load_failure_has_one_redacted_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_path_token = "-".join(("private", "load", "location", "5f34c9"))
    private_parser_token = "-".join(("private", "json", "token", "a7d122"))
    private_sentinel_token = "-".join(("private", "parser", "sentinel", "96e02c"))
    raw_json = (
        '{"pending":["' + private_parser_token + '"],"claims":{unterminated\n'
    )
    store = FileStore(tmp_path / private_path_token)
    queue = RunNowQueue(store)
    queue.enqueue("seed")
    queue.queue_file.write_text(raw_json, encoding="utf-8")
    snapshot = queue.queue_file.read_bytes()
    parse = json.loads

    def fail_with_private_parser_context(value: str) -> Any:
        try:
            return parse(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{private_sentinel_token}: {queue.queue_file}: {value}"
            ) from exc

    monkeypatch.setattr(json, "loads", fail_with_private_parser_context)

    with pytest.raises(
        RExecOpValidationError,
        match=f"^{QUEUE_CLAIM_RECOVERY_BLOCKED}$",
    ) as caught:
        queue.list_pending()

    rendered = "".join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__ is True
    assert "JSONDecodeError" not in rendered
    assert "json.decoder" not in rendered
    assert private_path_token not in rendered
    assert private_parser_token not in rendered
    assert private_sentinel_token not in rendered
    assert raw_json.strip() not in rendered
    assert queue.queue_file.read_bytes() == snapshot


@pytest.mark.parametrize("kind", ["file", "memory", "sqlite"])
@pytest.mark.parametrize("attempt_status", ["started", "completed", "failed", "indeterminate"])
def test_approved_operation_with_any_attempt_fails_closed_in_logical_store(
    kind: str,
    attempt_status: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(kind, tmp_path, monkeypatch)
    operation = _operation(f"op-attempt-{kind}-{attempt_status}")
    old_lease = _claimed_expired_operation(store, operation)
    attempt = _start_attempt(store, operation.id, old_lease)
    if attempt_status != "started":
        store.finish_execution_attempt(attempt, status=attempt_status)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")

    with pytest.raises(RExecOpValidationError) as caught:
        _recover_claims(store, recovery_lease, observed_at=OBSERVED_AT)

    assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
    payload = _queue_payload(store)
    assert payload["pending"] == []
    assert payload["claims"][operation.id]["status"] == "claimed"
    blocker = payload["claims"][operation.id]["last_transition"]
    assert blocker["disposition"] == "blocked"
    assert blocker["reason"] == "approved_operation_has_attempts"
    assert blocker["attempt_count"] == 1
    serialized = json.dumps(blocker, sort_keys=True)
    assert "owner_token" not in serialized
    assert old_lease["owner_token"] not in serialized
    assert recovery_lease["owner_token"] not in serialized


@pytest.mark.parametrize(
    "operation_state",
    [
        "planned",
        "waiting_for_approval",
        "blocked",
        "running",
        "paused",
        "resuming",
        "retrying",
        "validating",
    ],
)
def test_nonapproved_nonterminal_states_never_requeue_directly(
    operation_state: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation(f"op-state-{operation_state}", state=operation_state)
    _claimed_expired_operation(store, operation)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")

    with pytest.raises(RExecOpValidationError) as caught:
        _recover_claims(store, recovery_lease, observed_at=OBSERVED_AT)

    assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
    payload = _queue_payload(store)
    assert payload["pending"] == []
    assert payload["claims"][operation.id]["status"] == "claimed"
    expected_reason = (
        "active_operation_requires_startup_recovery"
        if operation_state in {"running", "paused", "resuming", "retrying", "validating"}
        else "operation_state_not_recoverable"
    )
    assert payload["claims"][operation.id]["last_transition"]["reason"] == expected_reason


@pytest.mark.parametrize("attempt_status", ["pending", "started"])
def test_terminal_operation_waits_for_unfinished_attempt_recovery(
    attempt_status: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation(f"op-terminal-{attempt_status}", state="completed")
    old_lease = _claimed_expired_operation(store, operation)
    attempt = _start_attempt(store, operation.id, old_lease)
    if attempt_status == "pending":
        attempt_path = (
            store.root / "attempts" / operation.id / f"{attempt['attempt_id']}.json"
        )
        payload = json.loads(attempt_path.read_text(encoding="utf-8"))
        payload["status"] = "pending"
        payload.pop("started_at", None)
        _write_payload(attempt_path, payload)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")

    with pytest.raises(RExecOpValidationError) as caught:
        _recover_claims(store, recovery_lease, observed_at=OBSERVED_AT)

    assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
    payload = _queue_payload(store)
    assert payload["pending"] == []
    assert payload["claims"][operation.id]["last_transition"]["reason"] == (
        "unfinished_attempt_requires_recovery"
    )


@pytest.mark.parametrize(
    ("initial_state", "attempt_status", "expected_attempts"),
    [
        ("running", None, 0),
        ("running", "started", 1),
        ("running", "completed", 0),
        ("completed", None, 0),
    ],
)
def test_startup_orders_operation_and_attempt_recovery_before_claim_reconciliation(
    initial_state: str,
    attempt_status: str | None,
    expected_attempts: int,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation(f"op-startup-{initial_state}-{attempt_status}", state=initial_state)
    old_lease = _claimed_expired_operation(store, operation)
    if attempt_status is not None:
        attempt = _start_attempt(store, operation.id, old_lease)
        if attempt_status != "started":
            store.finish_execution_attempt(attempt, status=attempt_status)
    recovery_lease = store.acquire_execution_lease(worker_id="startup-recovery")
    controller = OperationController(store)

    report = run_startup_recovery(
        store,
        controller=controller,
        now=OBSERVED_AT,
        lease_record=recovery_lease,
        repair_receipts=False,
    )

    payload = _queue_payload(store)
    assert payload["pending"] == []
    assert payload["claims"][operation.id]["status"] == "completed"
    assert report["summary"]["changed"] is True
    assert report["summary"]["indeterminate_attempt_count"] == expected_attempts
    assert set(report["actions"]) == {
        "cleared_stale_worker_lease",
        "released_stale_locks",
        "interrupted_operations",
        "indeterminate_attempts",
        "receipt_repairs",
        "receipt_blockers",
        "projection_reconciliation",
    }
    if initial_state == "running":
        assert store.load_operation(operation.id).state == "failed"


def test_post_claim_pre_transition_crash_replays_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    assert operation.state == "approved"
    store.queue_enqueue(operation.id)
    old_lease = store.acquire_execution_lease(worker_id="crashed-worker")
    assert store.queue_claim(old_lease) is not None
    _expire_claim_and_worker(store, operation.id)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")
    run_startup_recovery(
        store,
        controller=controller,
        now=OBSERVED_AT,
        lease_record=recovery_lease,
        repair_receipts=False,
    )
    store.release_execution_lease(recovery_lease)

    invocations: list[str] = []
    original_invoke = StaticFixtureRuntime.invoke

    def record_invoke(runtime: StaticFixtureRuntime, request: Any) -> Any:
        invocations.append(request.action)
        return original_invoke(runtime, request)

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)

    assert controller.process_queue() == [operation.id]
    assert controller.process_queue() == []
    assert invocations == ["apply_fixture_change"]
    assert controller.get_operation(operation.id).state == "completed"


@pytest.mark.parametrize("window", ["before_defer", "after_defer_before_metadata"])
def test_claim_deferral_crash_windows_preserve_recoverable_queue_state(
    window: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )
    blocker = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    deferred = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target-2",
        mode="apply",
    )
    controller.advance(blocker.id)
    controller.start(deferred.id)
    invocations: list[str] = []

    def reject_invoke(_runtime: StaticFixtureRuntime, _request: Any) -> Any:
        invocations.append("unexpected")
        raise AssertionError("connector I/O must remain unreachable")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)
    if window == "before_defer":

        def crash_before_defer(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("crash-before-defer")

        monkeypatch.setattr(
            controller.runtime.queue,
            "defer_claim_from_lease",
            crash_before_defer,
        )
    else:

        def crash_before_metadata(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("crash-before-metadata")

        monkeypatch.setattr(
            controller.runtime,
            "_record_deferred_claim",
            crash_before_metadata,
        )

    with pytest.raises(RuntimeError, match="^crash-before-(defer|metadata)$"):
        controller.process_queue()

    payload = _queue_payload(store)
    claim = payload["claims"][deferred.id]
    if window == "before_defer":
        assert payload["pending"] == []
        assert claim["status"] == "claimed"
    else:
        assert payload["pending"] == [deferred.id]
        assert claim["status"] == "requeued"
        assert claim["last_transition"]["reason"] == "max_concurrent_reached"
    assert invocations == []


@pytest.mark.parametrize("unrelated_status", ["claimed", "requeued"])
def test_rollback_preflight_failure_atomically_removes_only_exact_claim(
    unrelated_status: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )
    operation = _operation("op-rollback-cleanup")
    operation.metadata = {
        "derived_operation": {"kind": "rollback"},
        "queue": {"status": "pending", "reason": "fixture", "position": 0},
    }
    unrelated = _operation(f"op-unrelated-{unrelated_status}")
    store.save_operation(operation)
    store.save_operation(unrelated)
    invocations: list[str] = []
    forbidden_calls: list[str] = []
    drain_calls = 0
    blocked = RExecOpValidationError("rollback preflight blocked")

    def block_preflight(_operation: Operation) -> Any:
        raise blocked

    def reject_legacy_cleanup(*_args: Any, **_kwargs: Any) -> None:
        forbidden_calls.append("legacy_cleanup")
        raise AssertionError("legacy public queue cleanup must remain unreachable")

    def reject_invoke(_runtime: StaticFixtureRuntime, _request: Any) -> Any:
        invocations.append("unexpected")
        raise AssertionError("connector I/O must remain unreachable")

    def reject_runtime_path(*_args: Any, **_kwargs: Any) -> Any:
        forbidden_calls.append("runtime_path")
        raise AssertionError("execution lifecycle must remain unreachable")

    drain = controller._drain_queue

    def counted_drain() -> list[str]:
        nonlocal drain_calls
        drain_calls += 1
        return drain()

    with controller.execution_lease() as lease:
        store.queue_enqueue(unrelated.id)
        unrelated_claim = controller.runtime.queue.claim_from_lease(lease)
        assert unrelated_claim is not None
        assert unrelated_claim["operation_id"] == unrelated.id
        store.queue_enqueue(operation.id)
        if unrelated_status == "requeued":
            controller.runtime.queue.defer_claim_from_lease(
                unrelated.id,
                unrelated_claim,
                lease,
                reason="max_concurrent_reached",
            )
        before = _queue_payload(store)
        monkeypatch.setattr(
            controller.orchestrator,
            "preflight_rollback_authority",
            block_preflight,
        )
        monkeypatch.setattr(
            controller.runtime.queue,
            "complete_claim_from_lease",
            reject_legacy_cleanup,
        )
        monkeypatch.setattr(
            controller.runtime.queue,
            "remove",
            reject_legacy_cleanup,
        )
        monkeypatch.setattr(controller.runtime, "_release_target_only", reject_runtime_path)
        monkeypatch.setattr(controller.runtime, "release_operation", reject_runtime_path)
        monkeypatch.setattr(controller.orchestrator, "start", reject_runtime_path)
        monkeypatch.setattr(controller.orchestrator, "advance", reject_runtime_path)
        monkeypatch.setattr(controller.orchestrator, "cancel", reject_runtime_path)
        monkeypatch.setattr(controller.orchestrator, "retry", reject_runtime_path)
        monkeypatch.setattr(controller, "rollback", reject_runtime_path)
        monkeypatch.setattr(controller, "_drain_queue", counted_drain)
        monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

        with pytest.raises(RExecOpValidationError) as caught:
            controller.process_queue()

    after = _queue_payload(store)
    assert caught.value is blocked
    assert operation.id not in after["pending"]
    assert operation.id not in after["claims"]
    assert after["claims"][unrelated.id] == before["claims"][unrelated.id]
    assert after["pending"] == (
        [unrelated.id] if unrelated_status == "requeued" else []
    )
    persisted = store.load_operation(operation.id)
    assert "queue" not in persisted.metadata
    assert persisted.state == operation.state
    assert persisted.history == operation.history
    assert store.list_execution_attempts(operation.id) == []
    assert invocations == []
    assert forbidden_calls == []
    assert drain_calls == 1


@pytest.mark.parametrize("unrelated_status", ["claimed", "requeued"])
def test_atomic_claim_cleanup_persists_once_and_preserves_unrelated_state(
    unrelated_status: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    target = _operation("op-atomic-target")
    unrelated = _operation(f"op-atomic-unrelated-{unrelated_status}")
    store.save_operation(target)
    store.save_operation(unrelated)
    queue = StoreRunNowQueue(store)
    queue.enqueue(unrelated.id)
    lease = store.acquire_execution_lease(worker_id="atomic-worker")
    unrelated_claim = queue.claim_from_lease(lease)
    assert unrelated_claim is not None
    queue.enqueue(target.id)
    if unrelated_status == "requeued":
        queue.defer_claim_from_lease(
            unrelated.id,
            unrelated_claim,
            lease,
            reason="max_concurrent_reached",
        )
    target_claim = queue.claim_from_lease(lease)
    assert target_claim is not None
    assert target_claim["operation_id"] == target.id
    before = _queue_payload(store)
    saves: list[dict[str, Any]] = []
    original_save = RunNowQueue._save_unlocked

    def record_save(lifecycle: RunNowQueue, data: dict[str, Any]) -> None:
        saves.append(json.loads(json.dumps(data)))
        original_save(lifecycle, data)

    monkeypatch.setattr(RunNowQueue, "_save_unlocked", record_save)

    queue._complete_and_remove_claim_from_lease(
        target.id,
        lease,
        claim_snapshot=target_claim,
    )

    after = _queue_payload(store)
    assert len(saves) == 1
    assert target.id not in saves[0]["claims"]
    assert target.id not in after["claims"]
    assert target.id not in after["pending"]
    assert after["claims"][unrelated.id] == before["claims"][unrelated.id]
    assert after["pending"] == before["pending"]


def test_atomic_claim_cleanup_failure_before_save_preserves_claimed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    target = _operation("op-atomic-save-failure")
    store.save_operation(target)
    queue = StoreRunNowQueue(store)
    queue.enqueue(target.id)
    lease = store.acquire_execution_lease(worker_id="atomic-worker")
    claim = queue.claim_from_lease(lease)
    assert claim is not None
    queue_path = store.root / "queue" / "run_now.json"
    snapshot = queue_path.read_bytes()
    sentinel = RuntimeError("atomic-save-sentinel")

    def fail_before_save(_queue: RunNowQueue, _data: dict[str, Any]) -> None:
        raise sentinel

    monkeypatch.setattr(RunNowQueue, "_save_unlocked", fail_before_save)

    with pytest.raises(RuntimeError) as caught:
        queue._complete_and_remove_claim_from_lease(
            target.id,
            lease,
            claim_snapshot=claim,
        )

    assert caught.value is sentinel
    assert queue_path.read_bytes() == snapshot
    assert _queue_payload(store)["claims"][target.id]["status"] == "claimed"


@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("stale_snapshot", RExecOpConcurrencyConflict),
        ("stale_lease", RExecOpConcurrencyConflict),
        ("missing", RExecOpConcurrencyConflict),
        ("replaced", RExecOpConcurrencyConflict),
        ("completed", RExecOpConcurrencyConflict),
        ("repeat", RExecOpConcurrencyConflict),
        ("target_pending", RExecOpValidationError),
        ("invalid_unrelated_topology", RExecOpValidationError),
    ],
)
def test_atomic_claim_cleanup_conflicts_and_topology_blocks_are_byte_identical(
    case: str,
    expected_error: type[Exception],
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / case)
    target = _operation(f"op-atomic-{case}")
    store.save_operation(target)
    queue = StoreRunNowQueue(store)
    queue.enqueue(target.id)
    lease = store.acquire_execution_lease(worker_id="atomic-worker")
    claim = queue.claim_from_lease(lease)
    assert claim is not None
    supplied_claim = dict(claim)
    supplied_lease = dict(lease)
    queue_path = store.root / "queue" / "run_now.json"

    if case == "repeat":
        queue._complete_and_remove_claim_from_lease(
            target.id,
            lease,
            claim_snapshot=claim,
        )
    elif case == "stale_snapshot":
        supplied_claim["attempt"] = int(supplied_claim["attempt"]) + 1
    elif case == "stale_lease":
        supplied_lease["owner_token"] = "stale-owner"
    else:
        payload = _queue_payload(store)
        if case == "missing":
            payload["claims"].pop(target.id)
        elif case == "replaced":
            payload["claims"][target.id]["attempt"] += 1
        elif case == "completed":
            payload["claims"][target.id]["status"] = "completed"
        elif case == "target_pending":
            payload["pending"].append(target.id)
        else:
            payload["pending"] = ["unrelated", "unrelated"]
        _write_payload(queue_path, payload)
    snapshot = queue_path.read_bytes()

    with pytest.raises(expected_error) as caught:
        queue._complete_and_remove_claim_from_lease(
            target.id,
            supplied_lease,
            claim_snapshot=supplied_claim,
        )

    if expected_error is RExecOpConcurrencyConflict:
        assert str(caught.value) == (
            "concurrency_conflict: queue claim ownership lost " f"for {target.id}"
        )
    else:
        assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
    assert queue_path.read_bytes() == snapshot


def test_attempt_bearing_ambiguity_stops_before_connector_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    store.queue_enqueue(operation.id)
    old_lease = store.acquire_execution_lease(worker_id="crashed-worker")
    assert store.queue_claim(old_lease) is not None
    _start_attempt(store, operation.id, old_lease)
    _expire_claim_and_worker(store, operation.id)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")
    store.release_execution_lease(recovery_lease)
    invocations: list[str] = []

    def reject_invoke(_runtime: StaticFixtureRuntime, _request: Any) -> Any:
        invocations.append("unexpected")
        raise AssertionError("connector I/O must remain unreachable")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

    with pytest.raises(RExecOpValidationError) as caught:
        controller.process_queue()

    assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
    assert invocations == []
    assert controller.get_operation(operation.id).state == "approved"


@pytest.mark.parametrize(
    "failure",
    ["invalid_expiry", "missing_operation", "duplicate_pending", "unknown_claim_status"],
)
def test_corrupt_or_inconsistent_claims_fail_with_one_redacted_error(
    failure: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-corrupt")
    _claimed_expired_operation(store, operation)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")
    payload = _queue_payload(store)
    if failure == "invalid_expiry":
        payload["claims"][operation.id]["expires_at"] = "not-a-time"
    elif failure == "missing_operation":
        (store.operations_dir / f"{operation.id}.json").unlink()
    elif failure == "duplicate_pending":
        payload["pending"] = [operation.id, operation.id]
    else:
        payload["claims"][operation.id]["status"] = "unknown"
    _write_payload(store.root / "queue" / "run_now.json", payload)

    with pytest.raises(RExecOpValidationError) as caught:
        store.queue_claim(recovery_lease)

    assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
    assert recovery_lease["owner_token"] not in str(caught.value)
    assert _queue_payload(store)["pending"] == (
        [operation.id, operation.id] if failure == "duplicate_pending" else []
    )


@pytest.mark.parametrize("corruption", ["unknown_attempt_status", "non_object_queue"])
def test_malformed_persistence_never_reaches_dequeue(
    corruption: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-malformed")
    old_lease = _claimed_expired_operation(store, operation)
    if corruption == "unknown_attempt_status":
        attempt = _start_attempt(store, operation.id, old_lease)
        attempt_path = (
            store.root
            / "attempts"
            / operation.id
            / f"{attempt['attempt_id']}.json"
        )
        payload = json.loads(attempt_path.read_text(encoding="utf-8"))
        payload["status"] = "unknown"
        _write_payload(attempt_path, payload)
    else:
        (store.root / "queue" / "run_now.json").write_text("[]\n", encoding="utf-8")
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")

    with pytest.raises(RExecOpValidationError) as caught:
        store.queue_claim(recovery_lease)

    assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
    assert recovery_lease["owner_token"] not in str(caught.value)


def _recover_contended(root: str, lease: dict[str, Any], barrier: Any, result: Any) -> None:
    barrier.wait(timeout=10)
    changed = _recover_claims(
        FileStore(Path(root)),
        lease,
        observed_at=OBSERVED_AT,
    )
    result.put(changed)


def _claim_contended(root: str, lease: dict[str, Any], barrier: Any, result: Any) -> None:
    barrier.wait(timeout=10)
    claim = FileStore(Path(root)).queue_claim(lease)
    result.put(None if claim is None else claim["operation_id"])


def _join_processes(processes: list[Any]) -> None:
    try:
        for process in processes:
            process.join(timeout=15)
            assert not process.is_alive()
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=5)


@pytest.mark.parametrize("action", ["recover", "claim", "complete", "atomic_remove"])
def test_lease_turnover_waits_for_the_whole_queue_transaction(
    action: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation(f"op-lease-guard-{action}")
    claim_snapshot: dict[str, Any] | None = None
    if action == "recover":
        _claimed_expired_operation(store, operation)
        lease = store.acquire_execution_lease(worker_id="recovery-worker")
    else:
        store.save_operation(operation)
        store.queue_enqueue(operation.id)
        lease = store.acquire_execution_lease(worker_id="queue-worker")
        if action in {"complete", "atomic_remove"}:
            claim_snapshot = store.queue_claim(lease)
            assert claim_snapshot is not None

    entered = Event()
    release_transaction = Event()
    sequence: list[str] = []
    operation_result: list[Any] = []
    turnover_result: list[dict[str, Any]] = []
    original_load = RunNowQueue._load_unlocked
    original_save = RunNowQueue._save_unlocked

    def pause_after_guard(queue: RunNowQueue) -> dict[str, Any]:
        payload = original_load(queue)
        entered.set()
        if not release_transaction.wait(timeout=10):
            raise RuntimeError("queue transaction release was not signalled")
        return payload

    def record_persistence(queue: RunNowQueue, payload: dict[str, Any]) -> None:
        original_save(queue, payload)
        sequence.append("queue_persisted")

    monkeypatch.setattr(RunNowQueue, "_load_unlocked", pause_after_guard)
    monkeypatch.setattr(RunNowQueue, "_save_unlocked", record_persistence)

    def run_queue_transaction() -> None:
        if action == "recover":
            operation_result.append(
                _recover_claims(store, lease, observed_at=OBSERVED_AT)
            )
        elif action == "claim":
            operation_result.append(store.queue_claim(lease))
        elif action == "complete":
            store.queue_complete_claim(operation.id, lease)
            operation_result.append(True)
        else:
            assert claim_snapshot is not None
            StoreRunNowQueue(store)._complete_and_remove_claim_from_lease(
                operation.id,
                lease,
                claim_snapshot=claim_snapshot,
            )
            operation_result.append(True)

    turnover_gate = Barrier(2)

    def advance_lease() -> None:
        turnover_gate.wait(timeout=10)
        advanced = store.acquire_execution_lease(worker_id="next-worker")
        turnover_result.append(advanced)
        sequence.append("lease_advanced")

    transaction_thread = Thread(target=run_queue_transaction, daemon=True)
    turnover_thread = Thread(target=advance_lease, daemon=True)
    turnover_started = False
    transaction_thread.start()
    try:
        assert entered.wait(timeout=10)
        exact_lock_path = store.root / "watchdog" / "worker_lease.lock"
        with exact_lock_path.open("a+", encoding="utf-8") as lock_probe:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock_probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lease_path = store.root / "watchdog" / "worker_lease.json"
        lease_payload = json.loads(lease_path.read_text(encoding="utf-8"))
        lease_payload["expires_at"] = "2000-01-01T00:00:00+00:00"
        _write_payload(lease_path, lease_payload)
        turnover_thread.start()
        turnover_started = True
        turnover_gate.wait(timeout=10)
    finally:
        release_transaction.set()
        transaction_thread.join(timeout=10)
        if turnover_started:
            turnover_thread.join(timeout=10)

    assert not transaction_thread.is_alive()
    assert turnover_started and not turnover_thread.is_alive()
    assert operation_result
    assert turnover_result[0]["lease_epoch"] == lease["lease_epoch"] + 1
    assert sequence == ["queue_persisted", "lease_advanced"]


def test_recovery_and_next_claim_serialize_to_single_transitions(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-contended-recovery")
    _claimed_expired_operation(store, operation)
    recovery_lease = store.acquire_execution_lease(worker_id="recovery-worker")
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    barrier = context.Barrier(2)
    recoverers = [
        context.Process(
            target=_recover_contended,
            args=(str(store.root), recovery_lease, barrier, result),
        )
        for _ in range(2)
    ]
    for process in recoverers:
        process.start()
    _join_processes(recoverers)

    assert sorted(result.get(timeout=2) for _ in recoverers) == [False, True]
    payload = _queue_payload(store)
    assert payload["pending"] == [operation.id]
    assert payload["claims"][operation.id]["last_transition"]["disposition"] == "requeued"
    snapshot = (store.root / "queue" / "run_now.json").read_bytes()
    assert not _recover_claims(store, recovery_lease, observed_at=OBSERVED_AT)
    assert (store.root / "queue" / "run_now.json").read_bytes() == snapshot

    result = context.Queue()
    barrier = context.Barrier(2)
    claimers = [
        context.Process(
            target=_claim_contended,
            args=(str(store.root), recovery_lease, barrier, result),
        )
        for _ in range(2)
    ]
    for process in claimers:
        process.start()
    _join_processes(claimers)

    assert sorted((result.get(timeout=2) for _ in claimers), key=str) == [
        None,
        operation.id,
    ]
    assert _queue_payload(store)["claims"][operation.id]["attempt"] == 2


@pytest.mark.parametrize("kind", ["file", "memory", "sqlite"])
@pytest.mark.parametrize(
    ("state", "attempt_status", "expected_purpose"),
    [
        ("approved", None, "execution"),
        ("completed", "completed", "terminal_cleanup"),
        ("failed", "failed", "terminal_cleanup"),
        ("cancelled", "indeterminate", "terminal_cleanup"),
        ("escalated", None, "terminal_cleanup"),
    ],
)
def test_claim_specific_returns_ephemeral_purpose_and_exact_baseline_snapshot(
    kind: str,
    state: str,
    attempt_status: str | None,
    expected_purpose: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(kind, tmp_path, monkeypatch)
    operation = _operation(f"op-specific-purpose-{kind}-{state}", state=state)
    store.save_operation(operation)
    store.queue_enqueue(operation.id)
    lease = store.acquire_execution_lease(worker_id="specific-worker")
    if attempt_status is not None:
        attempt = _start_attempt(store, operation.id, lease)
        store.finish_execution_attempt(attempt, status=attempt_status)

    selected = StoreRunNowQueue(store).claim_specific_from_lease(
        operation.id,
        lease,
    )

    assert selected is not None
    assert set(selected) == {"purpose", "claim"}
    assert selected["purpose"] == expected_purpose
    claim = selected["claim"]
    assert set(claim) == {
        "operation_id",
        "status",
        "owner_token",
        "process_instance_id",
        "lease_epoch",
        "attempt",
        "claimed_at",
        "expires_at",
    }
    persisted_claim = _queue_payload(store)["claims"][operation.id]
    assert claim == persisted_claim
    assert "purpose" not in persisted_claim


@pytest.mark.parametrize(
    "case",
    [
        "approved_started",
        "approved_completed",
        "approved_failed",
        "approved_indeterminate",
        "terminal_pending",
        "terminal_started",
        "active",
        "nonterminal",
        "missing_operation",
        "malformed_inventory",
    ],
)
def test_claim_specific_rejected_logical_facts_are_byte_identical_and_zero_io(
    case: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    state = {
        "terminal_pending": "completed",
        "terminal_started": "failed",
        "active": "running",
        "nonterminal": "planned",
    }.get(case, "approved")
    operation = _operation(f"op-specific-rejected-{case}", state=state)
    if case != "missing_operation":
        store.save_operation(operation)
    store.queue_enqueue(operation.id)
    lease = store.acquire_execution_lease(worker_id="specific-worker")
    if case in {
        "approved_started",
        "approved_completed",
        "approved_failed",
        "approved_indeterminate",
        "terminal_pending",
        "terminal_started",
        "malformed_inventory",
    }:
        attempt = _start_attempt(store, operation.id, lease)
        terminal_status = case.removeprefix("approved_")
        if terminal_status in {"completed", "failed", "indeterminate"}:
            store.finish_execution_attempt(attempt, status=terminal_status)
        if case in {"terminal_pending", "malformed_inventory"}:
            attempt_path = (
                store.root
                / "attempts"
                / operation.id
                / f"{attempt['attempt_id']}.json"
            )
            attempt_payload = json.loads(attempt_path.read_text(encoding="utf-8"))
            attempt_payload["status"] = (
                "pending" if case == "terminal_pending" else "unknown"
            )
            _write_payload(attempt_path, attempt_payload)
    snapshot = (store.root / "queue" / "run_now.json").read_bytes()
    invocations: list[str] = []

    def reject_invoke(_runtime: StaticFixtureRuntime, _request: Any) -> Any:
        invocations.append("unexpected")
        raise AssertionError("connector I/O must remain unreachable")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

    with pytest.raises(
        RExecOpValidationError,
        match=f"^{QUEUE_CLAIM_RECOVERY_BLOCKED}$",
    ):
        StoreRunNowQueue(store).claim_specific_from_lease(operation.id, lease)

    assert (store.root / "queue" / "run_now.json").read_bytes() == snapshot
    assert invocations == []


def test_claim_specific_existing_claim_always_conflicts_byte_identically(
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-specific-existing-claim")
    store.save_operation(operation)
    store.queue_enqueue(operation.id)
    lease = store.acquire_execution_lease(worker_id="specific-worker")
    assert store.queue_claim(lease) is not None
    snapshot = (store.root / "queue" / "run_now.json").read_bytes()

    with pytest.raises(RExecOpConcurrencyConflict):
        StoreRunNowQueue(store).claim_specific_from_lease(operation.id, lease)

    assert (store.root / "queue" / "run_now.json").read_bytes() == snapshot


@pytest.mark.parametrize(
    ("queue_state", "expected_attempt"),
    [("bare", 1), ("completed_pending", 2), ("requeued", 2)],
)
def test_claim_specific_consumes_each_approved_pending_state_exactly(
    queue_state: str,
    expected_attempt: int,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation(f"op-specific-state-{queue_state}")
    store.save_operation(operation)
    store.queue_enqueue(operation.id)
    lease = store.acquire_execution_lease(worker_id="specific-worker")
    queue = StoreRunNowQueue(store)
    previous_transition: dict[str, Any] | None = None
    if queue_state != "bare":
        initial = queue.claim_from_lease(lease)
        assert initial is not None
        if queue_state == "completed_pending":
            queue.complete_claim_from_lease(
                operation.id,
                lease,
                claim_snapshot=initial,
            )
            queue.enqueue(operation.id)
        else:
            queue.defer_claim_from_lease(
                operation.id,
                initial,
                lease,
                reason="max_concurrent_reached",
            )
        previous_transition = dict(
            _queue_payload(store)["claims"][operation.id]["last_transition"]
        )

    selected = queue.claim_specific_from_lease(operation.id, lease)

    assert selected is not None
    assert selected["purpose"] == "execution"
    assert selected["claim"]["attempt"] == expected_attempt
    assert _queue_payload(store)["pending"] == []
    if previous_transition is not None:
        assert selected["claim"]["last_transition"] == previous_transition


def test_claim_specific_no_pending_or_claim_is_nonpersisting_none(
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-specific-none")
    store.save_operation(operation)
    lease = store.acquire_execution_lease(worker_id="specific-worker")
    queue_file = store.root / "queue" / "run_now.json"

    assert (
        StoreRunNowQueue(store).claim_specific_from_lease(operation.id, lease)
        is None
    )
    assert not queue_file.exists()


def test_claim_specific_legacy_claimed_pending_fails_closed_byte_identically(
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation("op-specific-legacy-claimed-pending")
    store.save_operation(operation)
    store.queue_enqueue(operation.id)
    lease = store.acquire_execution_lease(worker_id="specific-worker")
    assert store.queue_claim(lease) is not None
    payload = _queue_payload(store)
    payload["pending"] = [operation.id]
    _write_payload(store.root / "queue" / "run_now.json", payload)
    snapshot = (store.root / "queue" / "run_now.json").read_bytes()

    with pytest.raises(
        RExecOpValidationError,
        match=f"^{QUEUE_CLAIM_RECOVERY_BLOCKED}$",
    ):
        StoreRunNowQueue(store).claim_specific_from_lease(operation.id, lease)

    assert (store.root / "queue" / "run_now.json").read_bytes() == snapshot


def test_claim_specific_selects_non_head_then_fifo_claims_remaining_head(
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    first = _operation("op-specific-first")
    selected_operation = _operation("op-specific-selected")
    for operation in (first, selected_operation):
        store.save_operation(operation)
        store.queue_enqueue(operation.id)
    lease = store.acquire_execution_lease(worker_id="specific-worker")
    queue = StoreRunNowQueue(store)

    selected = queue.claim_specific_from_lease(selected_operation.id, lease)
    fifo = queue.claim_from_lease(lease)

    assert selected is not None
    assert selected["purpose"] == "execution"
    assert selected["claim"]["operation_id"] == selected_operation.id
    assert fifo is not None
    assert fifo["operation_id"] == first.id
    assert _queue_payload(store)["pending"] == []


@pytest.mark.parametrize("contender", ["fifo", "specific"])
def test_claim_specific_contenders_serialize_to_exactly_one_winner(
    contender: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation(f"op-specific-contender-{contender}")
    store.save_operation(operation)
    store.queue_enqueue(operation.id)
    lease = store.acquire_execution_lease(worker_id="shared-worker")
    barrier = Barrier(3)
    results: list[tuple[str, str]] = []

    def run_specific(label: str) -> None:
        barrier.wait(timeout=10)
        try:
            result = StoreRunNowQueue(store).claim_specific_from_lease(
                operation.id,
                lease,
            )
        except RExecOpConcurrencyConflict:
            results.append((label, "conflict"))
        else:
            results.append((label, "winner" if result is not None else "none"))

    def run_fifo() -> None:
        barrier.wait(timeout=10)
        result = StoreRunNowQueue(store).claim_from_lease(lease)
        results.append(("fifo", "winner" if result is not None else "none"))

    threads = [Thread(target=run_specific, args=("specific-1",), daemon=True)]
    if contender == "fifo":
        threads.append(Thread(target=run_fifo, daemon=True))
    else:
        threads.append(
            Thread(target=run_specific, args=("specific-2",), daemon=True)
        )
    for thread in threads:
        thread.start()
    barrier.wait(timeout=10)
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert sum(outcome == "winner" for _, outcome in results) == 1
    assert len(results) == 2
    if contender == "specific":
        assert sum(outcome == "conflict" for _, outcome in results) == 1
    else:
        assert sorted(outcome for _, outcome in results) in [
            ["conflict", "winner"],
            ["none", "winner"],
        ]
    payload = _queue_payload(store)
    assert payload["pending"] == []
    assert payload["claims"][operation.id]["status"] == "claimed"
    assert payload["claims"][operation.id]["attempt"] == 1


@pytest.mark.parametrize(
    "queue_state",
    ["none", "bare", "claimed", "requeued", "completed_pending", "terminal_attempt"],
)
def test_remove_cancelled_from_lease_is_atomic_and_idempotent(
    queue_state: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation(f"op-cancel-cleanup-{queue_state}")
    store.save_operation(operation)
    if queue_state != "none":
        store.queue_enqueue(operation.id)
    lease = store.acquire_execution_lease(worker_id="cancel-worker")
    queue = StoreRunNowQueue(store)
    if queue_state in {"claimed", "requeued", "completed_pending"}:
        selected = queue.claim_specific_from_lease(operation.id, lease)
        assert selected is not None
        claim = selected["claim"]
        if queue_state == "requeued":
            queue.defer_claim_from_lease(
                operation.id,
                claim,
                lease,
                reason="max_concurrent_reached",
            )
        elif queue_state == "completed_pending":
            queue.complete_claim_from_lease(
                operation.id,
                lease,
                claim_snapshot=claim,
            )
            queue.enqueue(operation.id)
    if queue_state == "terminal_attempt":
        attempt = _start_attempt(store, operation.id, lease)
        store.finish_execution_attempt(attempt, status="failed")
    operation.state = "cancelled"
    store.save_operation(operation)

    queue.remove_cancelled_from_lease(operation.id, lease)

    payload = _queue_payload(store) if (store.root / "queue" / "run_now.json").is_file() else {
        "pending": [],
        "claims": {},
    }
    assert operation.id not in payload["pending"]
    assert operation.id not in payload["claims"]
    snapshot = (
        (store.root / "queue" / "run_now.json").read_bytes()
        if (store.root / "queue" / "run_now.json").is_file()
        else None
    )
    queue.remove_cancelled_from_lease(operation.id, lease)
    repeated = (
        (store.root / "queue" / "run_now.json").read_bytes()
        if (store.root / "queue" / "run_now.json").is_file()
        else None
    )
    assert repeated == snapshot


@pytest.mark.parametrize("attempt_status", ["pending", "started"])
def test_remove_cancelled_rejects_unfinished_attempt_byte_identically(
    attempt_status: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = _operation(f"op-cancel-unfinished-{attempt_status}")
    store.save_operation(operation)
    store.queue_enqueue(operation.id)
    lease = store.acquire_execution_lease(worker_id="cancel-worker")
    attempt = _start_attempt(store, operation.id, lease)
    if attempt_status == "pending":
        attempt_path = (
            store.root / "attempts" / operation.id / f"{attempt['attempt_id']}.json"
        )
        payload = json.loads(attempt_path.read_text(encoding="utf-8"))
        payload["status"] = "pending"
        _write_payload(attempt_path, payload)
    operation.state = "cancelled"
    store.save_operation(operation)
    snapshot = (store.root / "queue" / "run_now.json").read_bytes()

    with pytest.raises(
        RExecOpValidationError,
        match=f"^{QUEUE_CLAIM_RECOVERY_BLOCKED}$",
    ):
        StoreRunNowQueue(store).remove_cancelled_from_lease(operation.id, lease)

    assert (store.root / "queue" / "run_now.json").read_bytes() == snapshot


def test_drain_delegates_exact_claim_completion_to_start_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.runtime._mark_queued(operation, reason="fixture-queued")
    invocations: list[str] = []
    completions: list[dict[str, Any]] = []
    inside_start = False
    invoke = StaticFixtureRuntime.invoke
    start = controller._start_operation
    complete = controller.runtime.queue.complete_claim_from_lease

    def record_invoke(runtime: StaticFixtureRuntime, request: Any) -> Any:
        invocations.append(request.action)
        return invoke(runtime, request)

    def wrapped_start(operation_id: str, **kwargs: Any) -> Operation:
        nonlocal inside_start
        inside_start = True
        try:
            return start(operation_id, **kwargs)
        finally:
            inside_start = False

    def wrapped_complete(
        operation_id: str,
        lease: dict[str, Any],
        *,
        claim_snapshot: dict[str, Any] | None = None,
    ) -> None:
        assert inside_start
        assert claim_snapshot is not None
        completions.append(dict(claim_snapshot))
        complete(
            operation_id,
            lease,
            claim_snapshot=claim_snapshot,
        )

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)
    monkeypatch.setattr(controller, "_start_operation", wrapped_start)
    monkeypatch.setattr(
        controller.runtime.queue,
        "complete_claim_from_lease",
        wrapped_complete,
    )

    assert controller.process_queue() == [operation.id]

    payload = _queue_payload(store)
    assert payload["pending"] == []
    assert payload["claims"][operation.id]["status"] == "completed"
    assert len(completions) == 1
    assert completions[0]["operation_id"] == operation.id
    assert completions[0]["status"] == "claimed"
    assert "purpose" not in completions[0]
    assert invocations == ["apply_fixture_change"]


def test_terminal_start_repairs_receipt_and_completes_queue_without_execution_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    assert controller.start(operation.id).state == "completed"
    receipt_path = store.receipts_dir / f"{operation.id}.json"
    assert receipt_path.is_file()
    receipt_path.unlink()
    store.queue_enqueue(operation.id)

    def reject(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("terminal cleanup must bypass execution checks and connector I/O")

    monkeypatch.setenv("REXECOP_MUTATION_POSTURE", "stable_read_only")
    monkeypatch.setattr(controller.orchestrator, "preflight_rollback_authority", reject)
    monkeypatch.setattr(controller.runtime, "check_maintenance_window", reject)
    monkeypatch.setattr(controller, "_verify_catalog_binding", reject)
    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject)

    reconciled = controller.start(operation.id)

    assert reconciled.state == "completed"
    assert receipt_path.is_file()
    payload = _queue_payload(store)
    assert payload["pending"] == []
    assert payload["claims"][operation.id]["status"] == "completed"
    assert "purpose" not in payload["claims"][operation.id]
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) is None


@pytest.mark.parametrize("entrypoint", ["public_admission", "direct_start"])
def test_unsupported_file_store_subclass_fails_before_runtime_mutation_or_io(
    entrypoint: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _UnsupportedFileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    operation_path = store.operations_dir / f"{operation.id}.json"
    operation_snapshot = operation_path.read_bytes()

    def reject_invoke(_runtime: StaticFixtureRuntime, _request: Any) -> Any:
        raise AssertionError("connector I/O must remain unreachable")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

    with pytest.raises(
        RExecOpValidationError,
        match=f"^{QUEUE_CLAIM_LIFECYCLE_UNSUPPORTED}$",
    ):
        if entrypoint == "public_admission":
            controller.runtime.admit_for_execution(operation)
        else:
            controller.start(operation.id)

    assert operation_path.read_bytes() == operation_snapshot
    assert not (store.root / "queue").exists()
    assert not (store.root / "locks").exists()
    assert store.list_execution_attempts(operation.id) == []


def _cancel_ready_operation(controller: OperationController) -> Operation:
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    operation = controller.advance(operation.id, max_steps=1)
    assert operation.state == "running"
    controller.runtime._mark_queued(operation, reason="fixture-queued")
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) == operation.id
    return operation


def test_cancel_orders_durable_state_queue_cleanup_target_release_then_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )
    operation = _cancel_ready_operation(controller)
    order: list[str] = []
    cancel = controller.orchestrator.cancel
    cleanup = controller.runtime.queue.remove_cancelled_from_lease
    release = controller.runtime._release_target_only

    def ordered_cancel(operation_id: str) -> Operation:
        result = cancel(operation_id)
        assert store.load_operation(operation_id).state == "cancelled"
        order.append("cancelled_persisted")
        return result

    def ordered_cleanup(operation_id: str, lease: dict[str, Any]) -> None:
        assert order == ["cancelled_persisted"]
        cleanup(operation_id, lease)
        payload = _queue_payload(store)
        assert operation_id not in payload["pending"]
        assert operation_id not in payload["claims"]
        order.append("queue_cleaned")

    def ordered_release(candidate: Operation) -> None:
        assert order == ["cancelled_persisted", "queue_cleaned"]
        release(candidate)
        assert controller.runtime.target_lock.holder_operation_id(
            candidate.environment,
            candidate.target,
        ) is None
        order.append("target_released")

    def ordered_drain() -> list[str]:
        assert order == [
            "cancelled_persisted",
            "queue_cleaned",
            "target_released",
        ]
        order.append("queue_drained")
        return []

    def reject_invoke(_runtime: StaticFixtureRuntime, _request: Any) -> Any:
        raise AssertionError("cancelled operation must not reach connector I/O")

    monkeypatch.setattr(controller.orchestrator, "cancel", ordered_cancel)
    monkeypatch.setattr(
        controller.runtime.queue,
        "remove_cancelled_from_lease",
        ordered_cleanup,
    )
    monkeypatch.setattr(controller.runtime, "_release_target_only", ordered_release)
    monkeypatch.setattr(controller, "_drain_queue", ordered_drain)
    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

    result = controller.cancel(operation.id)

    assert result.state == "cancelled"
    assert order == [
        "cancelled_persisted",
        "queue_cleaned",
        "target_released",
        "queue_drained",
    ]


@pytest.mark.parametrize("crash_window", ["after_cancel", "after_queue_cleanup"])
def test_repeated_cancel_repairs_declared_crash_window_and_is_idempotent(
    crash_window: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )
    operation = _cancel_ready_operation(controller)
    queue_path = store.root / "queue" / "run_now.json"
    initial_queue = queue_path.read_bytes()
    cleanup = controller.runtime.queue.remove_cancelled_from_lease
    release = controller.runtime._release_target_only

    def reject_invoke(_runtime: StaticFixtureRuntime, _request: Any) -> Any:
        raise AssertionError("cancel cleanup must not reach connector I/O")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)
    if crash_window == "after_cancel":

        def crash_after_cancel(*_args: Any, **_kwargs: Any) -> None:
            assert store.load_operation(operation.id).state == "cancelled"
            raise RuntimeError("crash-after-cancel")

        monkeypatch.setattr(
            controller.runtime.queue,
            "remove_cancelled_from_lease",
            crash_after_cancel,
        )
        expected_error = "crash-after-cancel"
    else:

        def crash_after_cleanup(_candidate: Operation) -> None:
            payload = _queue_payload(store)
            assert operation.id not in payload["pending"]
            assert operation.id not in payload["claims"]
            raise RuntimeError("crash-after-queue-cleanup")

        monkeypatch.setattr(
            controller.runtime,
            "_release_target_only",
            crash_after_cleanup,
        )
        expected_error = "crash-after-queue-cleanup"

    with pytest.raises(RuntimeError, match=f"^{expected_error}$"):
        controller.cancel(operation.id)

    assert store.load_operation(operation.id).state == "cancelled"
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) == operation.id
    if crash_window == "after_cancel":
        assert queue_path.read_bytes() == initial_queue
        monkeypatch.setattr(
            controller.runtime.queue,
            "remove_cancelled_from_lease",
            cleanup,
        )
    else:
        payload = _queue_payload(store)
        assert operation.id not in payload["pending"]
        assert operation.id not in payload["claims"]
        monkeypatch.setattr(controller.runtime, "_release_target_only", release)

    repaired = controller.cancel(operation.id)

    assert repaired.state == "cancelled"
    payload = _queue_payload(store)
    assert operation.id not in payload["pending"]
    assert operation.id not in payload["claims"]
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) is None
    operation_path = store.operations_dir / f"{operation.id}.json"
    queue_snapshot = queue_path.read_bytes()
    operation_snapshot = operation_path.read_bytes()

    assert controller.cancel(operation.id).state == "cancelled"
    assert queue_path.read_bytes() == queue_snapshot
    assert operation_path.read_bytes() == operation_snapshot


@pytest.mark.parametrize("attempt_status", ["pending", "started"])
def test_cancel_unfinished_attempt_blocks_before_target_release_or_drain(
    attempt_status: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )
    operation = _cancel_ready_operation(controller)
    with controller.execution_lease() as lease:
        attempt = _start_attempt(store, operation.id, lease)
    if attempt_status == "pending":
        attempt_path = (
            store.root / "attempts" / operation.id / f"{attempt['attempt_id']}.json"
        )
        payload = json.loads(attempt_path.read_text(encoding="utf-8"))
        payload["status"] = "pending"
        _write_payload(attempt_path, payload)
    queue_path = store.root / "queue" / "run_now.json"
    queue_snapshot = queue_path.read_bytes()
    release_calls: list[str] = []
    drain_calls: list[str] = []

    def reject_release(_candidate: Operation) -> None:
        release_calls.append("unexpected")
        raise AssertionError("target release must remain unreachable")

    def reject_drain() -> list[str]:
        drain_calls.append("unexpected")
        raise AssertionError("queue drain must remain unreachable")

    def reject_invoke(_runtime: StaticFixtureRuntime, _request: Any) -> Any:
        raise AssertionError("cancel cleanup must not reach connector I/O")

    monkeypatch.setattr(controller.runtime, "_release_target_only", reject_release)
    monkeypatch.setattr(controller, "_drain_queue", reject_drain)
    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

    with pytest.raises(
        RExecOpValidationError,
        match=f"^{QUEUE_CLAIM_RECOVERY_BLOCKED}$",
    ):
        controller.cancel(operation.id)

    assert store.load_operation(operation.id).state == "cancelled"
    assert queue_path.read_bytes() == queue_snapshot
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) == operation.id
    assert release_calls == []
    assert drain_calls == []


def test_approved_advance_with_existing_attempt_fails_closed_byte_identically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.runtime._mark_queued(operation, reason="fixture-queued")
    with controller.execution_lease() as lease:
        _start_attempt(store, operation.id, lease)
    queue_path = store.root / "queue" / "run_now.json"
    operation_path = store.operations_dir / f"{operation.id}.json"
    queue_snapshot = queue_path.read_bytes()
    operation_snapshot = operation_path.read_bytes()

    def reject_invoke(_runtime: StaticFixtureRuntime, _request: Any) -> Any:
        raise AssertionError("ambiguous approved attempt must not execute")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

    with pytest.raises(
        RExecOpValidationError,
        match=f"^{QUEUE_CLAIM_RECOVERY_BLOCKED}$",
    ):
        controller.advance(operation.id, max_steps=1)

    assert queue_path.read_bytes() == queue_snapshot
    assert operation_path.read_bytes() == operation_snapshot
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) is None
    assert len(store.list_execution_attempts(operation.id)) == 1


def test_terminal_direct_start_completes_exact_claim_before_trailing_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.runtime._mark_queued(operation, reason="fixture-queued")
    order: list[str] = []
    release = controller.runtime._release_target_only
    receipt = controller._ensure_terminal_receipt_if_needed
    complete = controller.runtime.queue.complete_claim_from_lease

    def ordered_release(candidate: Operation) -> None:
        assert candidate.state == "completed"
        assert _queue_payload(store)["claims"][candidate.id]["status"] == "claimed"
        release(candidate)
        order.append("target_released")

    def ordered_receipt(operation_id: str) -> None:
        assert order == ["target_released"]
        receipt(operation_id)
        assert (store.receipts_dir / f"{operation_id}.json").is_file()
        order.append("receipt_persisted")

    def ordered_complete(
        operation_id: str,
        lease: dict[str, Any],
        *,
        claim_snapshot: dict[str, Any] | None = None,
    ) -> None:
        assert order == ["target_released", "receipt_persisted"]
        assert claim_snapshot is not None
        complete(operation_id, lease, claim_snapshot=claim_snapshot)
        order.append("claim_completed")

    def ordered_drain() -> list[str]:
        assert order == [
            "target_released",
            "receipt_persisted",
            "claim_completed",
        ]
        assert _queue_payload(store)["claims"][operation.id]["status"] == "completed"
        order.append("queue_drained")
        return []

    monkeypatch.setattr(controller.runtime, "_release_target_only", ordered_release)
    monkeypatch.setattr(
        controller,
        "_ensure_terminal_receipt_if_needed",
        ordered_receipt,
    )
    monkeypatch.setattr(
        controller.runtime.queue,
        "complete_claim_from_lease",
        ordered_complete,
    )
    monkeypatch.setattr(controller, "_drain_queue", ordered_drain)

    completed = controller.start(operation.id)

    assert completed.state == "completed"
    assert order == [
        "target_released",
        "receipt_persisted",
        "claim_completed",
        "queue_drained",
    ]


def test_hard_terminal_receipt_failure_preserves_claim_until_recovery_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.runtime._mark_queued(operation, reason="fixture-queued")
    invocations: list[str] = []
    drain_calls: list[str] = []
    failure = RuntimeError("hard-terminal-receipt-failure")
    invoke = StaticFixtureRuntime.invoke
    receipt = controller._ensure_terminal_receipt_if_needed
    drain = controller._drain_queue

    def record_invoke(runtime: StaticFixtureRuntime, request: Any) -> Any:
        invocations.append(request.action)
        return invoke(runtime, request)

    def fail_receipt(_operation_id: str) -> None:
        (store.receipts_dir / f"{operation.id}.json").unlink(missing_ok=True)
        raise failure

    def record_drain() -> list[str]:
        drain_calls.append("drain")
        return drain()

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)
    monkeypatch.setattr(
        controller,
        "_ensure_terminal_receipt_if_needed",
        fail_receipt,
    )
    monkeypatch.setattr(controller, "_drain_queue", record_drain)

    with pytest.raises(RuntimeError) as caught:
        controller.start(operation.id)

    assert caught.value is failure
    assert controller.get_operation(operation.id).state == "completed"
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) is None
    assert not (store.receipts_dir / f"{operation.id}.json").exists()
    failed_payload = _queue_payload(store)
    assert failed_payload["pending"] == []
    assert failed_payload["claims"][operation.id]["status"] == "claimed"
    assert drain_calls == []
    assert invocations == ["apply_fixture_change"]

    failed_payload["claims"][operation.id]["expires_at"] = (
        "2000-01-01T00:00:00+00:00"
    )
    _write_payload(store.root / "queue" / "run_now.json", failed_payload)
    recovery_lease = store.acquire_execution_lease(worker_id="receipt-recovery")
    assert _recover_claims(
        store,
        recovery_lease,
        observed_at=OBSERVED_AT,
    )
    store.release_execution_lease(recovery_lease)
    assert _queue_payload(store)["claims"][operation.id]["status"] == "completed"
    assert not (store.receipts_dir / f"{operation.id}.json").exists()

    monkeypatch.setattr(
        controller,
        "_ensure_terminal_receipt_if_needed",
        receipt,
    )
    monkeypatch.setenv("REXECOP_MUTATION_POSTURE", "stable_read_only")

    repaired = controller.start(operation.id)

    assert repaired.state == "completed"
    assert (store.receipts_dir / f"{operation.id}.json").is_file()
    assert _queue_payload(store)["claims"][operation.id]["status"] == "completed"
    assert drain_calls == ["drain"]
    assert invocations == ["apply_fixture_change"]
