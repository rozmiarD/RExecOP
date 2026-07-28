from __future__ import annotations

import multiprocessing
from pathlib import Path
from typing import Any

import pytest

from rexecop.errors import RExecOpConcurrencyConflict, RExecOpValidationError
from rexecop.operation.model import Operation
from rexecop.runtime_ops.queue import RunNowQueue
from rexecop.runtime_ops.target_lock import TargetLockManager
from rexecop.storage.file_store import FileStore
from rexecop.storage.sqlite_store import SqliteStore

pytestmark = pytest.mark.m9_runtime


def _operation(operation_id: str = "op-cas") -> Operation:
    return Operation(
        id=operation_id,
        profile="fixture",
        environment="fixture",
        intent="inspect",
        target="target",
        mode="dry_run",
        requested_by="test",
        state="planned",
        created_at="2026-07-12T00:00:00+00:00",
        updated_at="2026-07-12T00:00:00+00:00",
    )


@pytest.mark.parametrize("backend", ["file", "sqlite"])
def test_stale_operation_revision_fails_with_stable_conflict(tmp_path: Path, backend: str) -> None:
    store = (
        FileStore(tmp_path / ".rexecop")
        if backend == "file"
        else SqliteStore(tmp_path / ".rexecop")
    )
    operation = _operation()
    store.save_operation(operation)
    first = store.load_operation(operation.id)
    stale = store.load_operation(operation.id)
    first.metadata["writer"] = "first"
    store.save_operation(first)
    stale.metadata["writer"] = "stale"

    with pytest.raises(RExecOpConcurrencyConflict) as caught:
        store.save_operation(stale)

    assert caught.value.code == "concurrency_conflict"
    assert store.load_operation(operation.id).metadata["writer"] == "first"


def _claim_queue(root: str, lease: dict[str, Any], result: Any) -> None:
    claim = RunNowQueue(FileStore(Path(root))).claim_from_lease(lease)
    result.put(None if claim is None else claim["operation_id"])


def _join_owned_processes(processes: list[Any]) -> None:
    try:
        for process in processes:
            process.join(timeout=10)
            assert not process.is_alive(), f"child process did not stop: {process.name}"
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            if process.is_alive():
                process.join(timeout=10)


def _save_contended_permit(root: str, marker: str, barrier: Any, result: Any) -> None:
    permit = {
        "operation_id": "op-shared-permit",
        "step_id": "step-shared",
        "attempt_id": "attempt-shared",
        "marker": marker,
    }
    barrier.wait()
    try:
        FileStore(Path(root)).save_execution_permit(permit)
    except RExecOpValidationError as exc:
        result.put({"marker": marker, "saved": False, "error": str(exc)})
    else:
        result.put({"marker": marker, "saved": True, "error": ""})


def _contend_for_target(
    root: str,
    operation_id: str,
    barrier: Any,
    result: Any,
) -> None:
    manager = TargetLockManager(FileStore(Path(root)))
    barrier.wait()
    acquired = manager.try_acquire(
        environment="env-shared",
        target="target-shared",
        operation_id=operation_id,
    )
    if not acquired:
        manager.release(
            environment="env-shared",
            target="target-shared",
            operation_id=operation_id,
        )
    result.put(
        {
            "operation_id": operation_id,
            "acquired": acquired,
            "release_attempted": not acquired,
        }
    )


def _take_over_stale_target(
    root: str,
    operation_id: str,
    barrier: Any,
    takeover_complete: Any,
    result: Any,
) -> None:
    manager = TargetLockManager(FileStore(Path(root)))
    barrier.wait()
    acquired = manager.try_acquire(
        environment="env-stale",
        target="target-stale",
        operation_id=operation_id,
    )
    if acquired:
        takeover_complete.set()
    result.put({"kind": "contender", "operation_id": operation_id, "acquired": acquired})


def _release_old_target_owner(
    root: str,
    barrier: Any,
    takeover_complete: Any,
    result: Any,
) -> None:
    manager = TargetLockManager(FileStore(Path(root)))
    barrier.wait()
    if not takeover_complete.wait(timeout=10):
        raise RuntimeError("target takeover did not complete")
    manager.release(
        environment="env-stale",
        target="target-stale",
        operation_id="op-old-owner",
    )
    result.put({"kind": "old_release", "operation_id": "op-old-owner"})


def _exercise_projection_failure(root: str, barrier: Any, result: Any) -> None:
    store = FileStore(Path(root))
    first = {
        "operation_id": "op-projection-failure",
        "step_id": "step-shared",
        "attempt_id": "attempt-first",
        "marker": "first",
    }
    attempt_path = (
        store.permits_dir / "op-projection-failure" / "attempts" / "attempt-first.json"
    )
    projection_path = store.permits_dir / "op-projection-failure" / "step-shared.json"
    original_write_json = store._write_json

    def fail_projection(path: Path, payload: dict[str, Any]) -> None:
        if path == projection_path:
            assert attempt_path.is_file()
            raise OSError("injected projection failure")
        original_write_json(path, payload)

    barrier.wait()
    store._write_json = fail_projection  # type: ignore[method-assign]
    try:
        store.save_execution_permit(first)
    except OSError as exc:
        projection_error = str(exc)
    else:
        raise AssertionError("projection failure was not injected")
    finally:
        store._write_json = original_write_json  # type: ignore[method-assign]

    immutable = store.load_execution_permit_for_attempt(
        "op-projection-failure",
        "attempt-first",
    )
    try:
        store.save_execution_permit(first)
    except RExecOpValidationError as exc:
        retry_error = str(exc)
    else:
        raise AssertionError("same attempt retry unexpectedly succeeded")

    second = dict(first, attempt_id="attempt-second", marker="second")
    store.save_execution_permit(second)
    result.put(
        {
            "projection_error": projection_error,
            "retry_error": retry_error,
            "immutable": immutable,
            "projection": store.load_execution_permit(
                "op-projection-failure",
                "step-shared",
            ),
        }
    )


def test_queue_claim_is_atomic_across_processes(tmp_path: Path) -> None:
    root = tmp_path / ".rexecop"
    store = FileStore(root)
    queue = RunNowQueue(store)
    queue.enqueue("op-first")
    lease = store.acquire_execution_lease(worker_id="queue-concurrency-test")
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    processes = [
        context.Process(target=_claim_queue, args=(str(root), lease, result)) for _ in range(2)
    ]
    for process in processes:
        process.start()
    _join_owned_processes(processes)

    assert sorted((result.get(timeout=2) for _ in processes), key=str) == [None, "op-first"]
    payload = queue._load_unlocked()
    claim = payload["claims"]["op-first"]
    assert claim["status"] == "claimed"
    assert claim["attempt"] == 1
    assert claim["owner_token"]
    assert claim["expires_at"]


def test_execution_permit_attempt_is_create_once_across_processes(tmp_path: Path) -> None:
    root = tmp_path / ".rexecop"
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    barrier = context.Barrier(4)
    processes = [
        context.Process(
            target=_save_contended_permit,
            args=(str(root), f"writer-{index}", barrier, result),
        )
        for index in range(4)
    ]
    for process in processes:
        process.start()
    _join_owned_processes(processes)

    outcomes = [result.get(timeout=2) for _ in processes]
    winners = [item for item in outcomes if item["saved"]]
    losers = [item for item in outcomes if not item["saved"]]
    assert len(winners) == 1
    assert len(losers) == 3
    assert {item["error"] for item in losers} == {"runtime attempt permit already exists"}
    store = FileStore(root)
    immutable = store.load_execution_permit_for_attempt(
        "op-shared-permit",
        "attempt-shared",
    )
    projection = store.load_execution_permit("op-shared-permit", "step-shared")
    assert immutable == projection
    assert immutable["marker"] == winners[0]["marker"]


def test_target_lock_has_one_active_winner_across_processes(tmp_path: Path) -> None:
    root = tmp_path / ".rexecop"
    store = FileStore(root)
    operation_ids = [f"op-contender-{index}" for index in range(4)]
    for operation_id in operation_ids:
        operation = _operation(operation_id)
        operation.state = "running"
        store.save_operation(operation)

    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    barrier = context.Barrier(4)
    processes = [
        context.Process(
            target=_contend_for_target,
            args=(str(root), operation_id, barrier, result),
        )
        for operation_id in operation_ids
    ]
    for process in processes:
        process.start()
    _join_owned_processes(processes)

    outcomes = [result.get(timeout=2) for _ in processes]
    winners = [item for item in outcomes if item["acquired"]]
    losers = [item for item in outcomes if not item["acquired"]]
    assert len(winners) == 1
    assert len(losers) == 3
    assert all(item["release_attempted"] for item in losers)
    winner_id = winners[0]["operation_id"]
    manager = TargetLockManager(store)
    winner_record = manager.read("env-shared", "target-shared")
    assert winner_record is not None
    assert winner_record["operation_id"] == winner_id
    assert manager.try_acquire(
        environment="env-shared",
        target="target-shared",
        operation_id=winner_id,
    )
    assert manager.read("env-shared", "target-shared") == winner_record


def test_stale_target_takeover_survives_delayed_old_owner_release(tmp_path: Path) -> None:
    root = tmp_path / ".rexecop"
    store = FileStore(root)
    old_owner = _operation("op-old-owner")
    old_owner.state = "completed"
    store.save_operation(old_owner)
    manager = TargetLockManager(store)
    assert manager.acquire(
        environment="env-stale",
        target="target-stale",
        operation_id=old_owner.id,
    )

    contender_ids = [f"op-new-owner-{index}" for index in range(3)]
    for operation_id in contender_ids:
        operation = _operation(operation_id)
        operation.state = "running"
        store.save_operation(operation)

    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    barrier = context.Barrier(4)
    takeover_complete = context.Event()
    processes = [
        context.Process(
            target=_take_over_stale_target,
            args=(str(root), operation_id, barrier, takeover_complete, result),
        )
        for operation_id in contender_ids
    ]
    processes.append(
        context.Process(
            target=_release_old_target_owner,
            args=(str(root), barrier, takeover_complete, result),
        )
    )
    for process in processes:
        process.start()
    _join_owned_processes(processes)

    outcomes = [result.get(timeout=2) for _ in processes]
    contenders = [item for item in outcomes if item["kind"] == "contender"]
    winners = [item for item in contenders if item["acquired"]]
    assert len(winners) == 1
    assert len(contenders) == 3
    assert {item["kind"] for item in outcomes} == {"contender", "old_release"}
    assert manager.holder_operation_id("env-stale", "target-stale") == winners[0][
        "operation_id"
    ]


def test_projection_failure_keeps_immutable_permit_and_allows_new_attempt(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".rexecop"
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    barrier = context.Barrier(1)
    process = context.Process(
        target=_exercise_projection_failure,
        args=(str(root), barrier, result),
    )
    process.start()
    _join_owned_processes([process])

    outcome = result.get(timeout=2)
    assert outcome["projection_error"] == "injected projection failure"
    assert outcome["retry_error"] == "runtime attempt permit already exists"
    assert outcome["immutable"]["attempt_id"] == "attempt-first"
    assert outcome["immutable"]["marker"] == "first"
    assert outcome["projection"]["attempt_id"] == "attempt-second"
    assert outcome["projection"]["marker"] == "second"
