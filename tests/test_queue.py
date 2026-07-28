from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
from rexecop.connectors.static_fixture import StaticFixtureRuntime
from rexecop.errors import RExecOpConcurrencyConflict, RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.operation.model import Operation
from rexecop.operation.state import OperationState
from rexecop.runtime_ops.queue import QUEUE_CLAIM_RECOVERY_BLOCKED, RunNowQueue
from rexecop.storage.file_store import FileStore
from runtime_governance_support import (
    governance_runtime_kwargs,
    governed_runtime_kwargs,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def _controller(tmp_path: Path, *, governed: bool = False) -> OperationController:
    runtime_kwargs = (
        governed_runtime_kwargs(
            target_namespaces=("fixture-target", "fixture-target-2"),
        )
        if governed
        else governance_runtime_kwargs()
    )
    return OperationController(
        store=FileStore(tmp_path / ".rexecop"),
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **runtime_kwargs,
    )


def _queue_operation(operation_id: str) -> Operation:
    return Operation(
        id=operation_id,
        profile="fixture",
        environment="fixture",
        intent="inspect",
        target="target",
        mode="dry_run",
        requested_by="test",
        state="approved",
        created_at="2026-07-27T00:00:00+00:00",
        updated_at="2026-07-27T00:00:00+00:00",
    )


def _queue_payload(store: FileStore) -> dict[str, Any]:
    return json.loads(
        (store.root / "queue" / "run_now.json").read_text(encoding="utf-8")
    )


def _write_queue_payload(store: FileStore, payload: dict[str, Any]) -> None:
    (store.root / "queue" / "run_now.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _public_mutation(
    queue: RunNowQueue,
    mutation: str,
    operation_id: str,
) -> str | None:
    if mutation == "discard":
        queue.discard_pending(operation_id)
        return None
    if mutation == "remove":
        queue.remove(operation_id)
        return None
    return queue.dequeue()


def test_queue_respects_max_concurrent_operations(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
) -> None:
    controller = _controller(tmp_path)
    first = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    second = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target-2",
        mode="apply",
    )
    controller.advance(first.id)
    queued = controller.start(second.id)
    assert queued.metadata["queue"]["reason"] == "max_concurrent_reached"
    assert controller.runtime.queue.list_pending() == [second.id]
    queue_file = controller.store.root / "queue" / "run_now.json"
    assert queue_file.stat().st_mode & 0o777 == 0o600
    assert queue_file.parent.stat().st_mode & 0o777 == 0o700


def test_process_queue_starts_next_operation(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
) -> None:
    controller = _controller(tmp_path, governed=True)
    first = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    second = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target-2",
        mode="apply",
    )
    controller.advance(first.id)
    controller.start(second.id)
    completed = controller.start(first.id)
    assert completed.state == OperationState.COMPLETED.value
    assert controller.get_operation(second.id).state == OperationState.COMPLETED.value


def test_repeated_capacity_defer_then_release_executes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_lab_mutation_runtime_test: None,
) -> None:
    controller = _controller(tmp_path, governed=True)
    invocations: list[str] = []
    invoke = StaticFixtureRuntime.invoke

    def record_invoke(runtime: StaticFixtureRuntime, request: Any) -> Any:
        invocations.append(request.action)
        return invoke(runtime, request)

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)
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
    queued = controller.start(deferred.id)
    assert queued.metadata["queue"]["reason"] == "max_concurrent_reached"
    assert invocations == []

    assert controller.process_queue() == []
    first_payload = json.loads(
        (controller.store.root / "queue" / "run_now.json").read_text(
            encoding="utf-8"
        )
    )
    first_claim = first_payload["claims"][deferred.id]
    assert first_payload["pending"] == [deferred.id]
    assert first_claim["status"] == "requeued"
    assert first_claim["last_transition"]["reason"] == "max_concurrent_reached"
    assert invocations == []

    assert controller.process_queue() == []
    second_payload = json.loads(
        (controller.store.root / "queue" / "run_now.json").read_text(
            encoding="utf-8"
        )
    )
    second_claim = second_payload["claims"][deferred.id]
    assert second_payload["pending"] == [deferred.id]
    assert second_claim["status"] == "requeued"
    assert second_claim["attempt"] == first_claim["attempt"] + 1
    assert invocations == []

    with controller.execution_lease():
        assert controller._start_operation(
            blocker.id,
            drain_queue=False,
        ).state == OperationState.COMPLETED.value
    deferred_payload = _queue_payload(controller.store)
    assert deferred_payload["pending"] == [deferred.id]
    assert deferred_payload["claims"][deferred.id]["status"] == "requeued"
    assert invocations == ["apply_fixture_change"]

    assert controller.start(deferred.id).state == OperationState.COMPLETED.value
    assert invocations == ["apply_fixture_change", "apply_fixture_change"]
    attempts = controller.store.list_execution_attempts(deferred.id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "completed"
    completed_payload = _queue_payload(controller.store)
    assert completed_payload["pending"] == []
    assert completed_payload["claims"][deferred.id]["status"] == "completed"
    assert controller.process_queue() == []
    assert invocations == ["apply_fixture_change", "apply_fixture_change"]


def test_public_admission_preserves_bare_pending_byte_identically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.runtime._mark_queued(operation, reason="fixture-queued")
    queue_path = controller.store.root / "queue" / "run_now.json"
    operation_path = controller.store.operations_dir / f"{operation.id}.json"
    queue_snapshot = queue_path.read_bytes()
    operation_snapshot = operation_path.read_bytes()

    def reject_invoke(_runtime: StaticFixtureRuntime, _request: Any) -> Any:
        raise AssertionError("connector I/O must remain unreachable")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

    assert controller.runtime.admit_for_execution(operation) == "queued"
    assert queue_path.read_bytes() == queue_snapshot
    assert operation_path.read_bytes() == operation_snapshot
    assert not (controller.store.root / "locks").exists()


@pytest.mark.parametrize(
    ("queue_state", "expected_error"),
    [
        ("claimed", RExecOpConcurrencyConflict),
        ("requeued", RExecOpConcurrencyConflict),
        ("invalid", RExecOpValidationError),
    ],
)
def test_public_admission_fails_closed_without_mutation_on_fenced_or_invalid_queue(
    queue_state: str,
    expected_error: type[Exception],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.runtime._mark_queued(operation, reason="fixture-queued")
    if queue_state == "claimed":
        assert RunNowQueue(controller.store).claim(
            owner_token="fixture-owner",
            lease_epoch=1,
            process_instance_id="public-process",
        ) is not None
    elif queue_state == "requeued":
        with controller.execution_lease() as lease:
            selection = controller.runtime.queue.claim_specific_from_lease(
                operation.id,
                lease,
            )
            assert selection is not None
            controller.runtime.queue.defer_claim_from_lease(
                operation.id,
                selection["claim"],
                lease,
                reason="max_concurrent_reached",
            )
    else:
        payload = _queue_payload(controller.store)
        payload["pending"].append(operation.id)
        _write_queue_payload(controller.store, payload)
    queue_path = controller.store.root / "queue" / "run_now.json"
    operation_path = controller.store.operations_dir / f"{operation.id}.json"
    queue_snapshot = queue_path.read_bytes()
    operation_snapshot = operation_path.read_bytes()

    def reject_invoke(_runtime: StaticFixtureRuntime, _request: Any) -> Any:
        raise AssertionError("connector I/O must remain unreachable")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

    with pytest.raises(expected_error) as caught:
        controller.runtime.admit_for_execution(operation)

    if queue_state == "invalid":
        assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
    assert queue_path.read_bytes() == queue_snapshot
    assert operation_path.read_bytes() == operation_snapshot
    assert not (controller.store.root / "locks").exists()


def test_approved_partial_advance_completes_claim_after_progress_and_running_reuses_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_lab_mutation_runtime_test: None,
) -> None:
    controller = _controller(tmp_path, governed=True)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.runtime._mark_queued(operation, reason="fixture-queued")
    completions: list[dict[str, Any]] = []
    invocations: list[str] = []
    complete = controller.runtime.queue.complete_claim_from_lease
    invoke = StaticFixtureRuntime.invoke

    def complete_after_progress(
        operation_id: str,
        lease: dict[str, Any],
        *,
        claim_snapshot: dict[str, Any] | None = None,
    ) -> None:
        progressed = controller.get_operation(operation_id)
        assert progressed.state == OperationState.RUNNING.value
        assert progressed.current_step_id == "pre_change_checkpoint"
        assert claim_snapshot is not None
        completions.append(dict(claim_snapshot))
        complete(operation_id, lease, claim_snapshot=claim_snapshot)

    def record_invoke(runtime: StaticFixtureRuntime, request: Any) -> Any:
        invocations.append(request.action)
        return invoke(runtime, request)

    monkeypatch.setattr(
        controller.runtime.queue,
        "complete_claim_from_lease",
        complete_after_progress,
    )
    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)

    first = controller.advance(operation.id, max_steps=1)

    assert first.state == OperationState.RUNNING.value
    assert first.current_step_id == "pre_change_checkpoint"
    assert len(completions) == 1
    assert completions[0]["status"] == "claimed"
    assert "purpose" not in completions[0]
    payload = _queue_payload(controller.store)
    assert payload["pending"] == []
    assert payload["claims"][operation.id]["status"] == "completed"
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) == operation.id
    assert not (controller.store.receipts_dir / f"{operation.id}.json").exists()
    assert invocations == []
    queue_snapshot = (
        controller.store.root / "queue" / "run_now.json"
    ).read_bytes()

    def reject_new_claim(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("RUNNING advance must not create a new queue claim")

    monkeypatch.setattr(
        controller.runtime.queue,
        "claim_specific_from_lease",
        reject_new_claim,
    )

    second = controller.advance(operation.id, max_steps=1)

    assert second.state == OperationState.RUNNING.value
    assert second.current_step_id == "apply_change"
    assert invocations == ["apply_fixture_change"]
    assert (
        controller.store.root / "queue" / "run_now.json"
    ).read_bytes() == queue_snapshot
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) == operation.id
    assert not (controller.store.receipts_dir / f"{operation.id}.json").exists()


def test_blocked_approved_advance_exact_defers_once_without_connector_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_lab_mutation_runtime_test: None,
) -> None:
    controller = _controller(tmp_path)
    blocker = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    candidate = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target-2",
        mode="apply",
    )
    assert controller.advance(blocker.id, max_steps=1).state == OperationState.RUNNING.value
    controller.runtime._mark_queued(candidate, reason="fixture-queued")

    def reject_invoke(_runtime: StaticFixtureRuntime, _request: Any) -> Any:
        raise AssertionError("blocked admission must not reach connector I/O")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

    deferred = controller.advance(candidate.id, max_steps=1)

    assert deferred.state == OperationState.APPROVED.value
    assert deferred.metadata["queue"]["reason"] == "max_concurrent_reached"
    payload = _queue_payload(controller.store)
    assert payload["pending"] == [candidate.id]
    claim = payload["claims"][candidate.id]
    assert claim["status"] == "requeued"
    assert claim["attempt"] == 1
    assert claim["last_transition"]["reason"] == "max_concurrent_reached"
    assert "purpose" not in claim
    assert controller.store.list_execution_attempts(candidate.id) == []
    assert controller.runtime.target_lock.holder_operation_id(
        candidate.environment,
        candidate.target,
    ) is None


def test_terminal_approved_advance_orders_release_receipt_completion_then_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_lab_mutation_runtime_test: None,
) -> None:
    controller = _controller(tmp_path, governed=True)
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
        assert candidate.state == OperationState.COMPLETED.value
        assert _queue_payload(controller.store)["claims"][candidate.id]["status"] == (
            "claimed"
        )
        release(candidate)
        order.append("target_released")

    def ordered_receipt(operation_id: str) -> None:
        assert order == ["target_released"]
        receipt(operation_id)
        assert (controller.store.receipts_dir / f"{operation_id}.json").is_file()
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
        assert _queue_payload(controller.store)["claims"][operation.id]["status"] == (
            "completed"
        )
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

    completed = controller.advance(operation.id, max_steps=10)

    assert completed.state == OperationState.COMPLETED.value
    assert order == [
        "target_released",
        "receipt_persisted",
        "claim_completed",
        "queue_drained",
    ]
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) is None


def test_public_claim_result_keeps_exact_head_compatible_keys(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    queue = RunNowQueue(store)
    queue.enqueue("op-public-claim-shape")

    claim = queue.claim(
        owner_token="fixture-owner",
        lease_epoch=1,
        process_instance_id="public-process",
    )

    assert claim is not None
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
    assert claim == _queue_payload(store)["claims"]["op-public-claim-shape"]
    assert "purpose" not in claim


@pytest.mark.parametrize("mutation", ["discard", "dequeue", "remove"])
def test_public_queue_mutators_empty_state_are_nonpersisting_noops(
    mutation: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    queue = RunNowQueue(store)

    assert _public_mutation(queue, mutation, "op-absent") is None
    assert not (store.root / "queue" / "run_now.json").exists()


@pytest.mark.parametrize("mutation", ["discard", "dequeue", "remove"])
@pytest.mark.parametrize("state", ["bare", "completed_pending"])
def test_public_queue_mutators_preserve_bare_and_completed_compatibility(
    mutation: str,
    state: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    queue = RunNowQueue(store)
    operation_id = f"op-public-compatible-{state}-{mutation}"
    queue.enqueue(operation_id)
    if state == "completed_pending":
        claim = queue.claim(
            owner_token="fixture-owner",
            lease_epoch=1,
            process_instance_id="public-process",
        )
        assert claim is not None
        queue.complete_claim(
            operation_id,
            owner_token="fixture-owner",
            lease_epoch=1,
        )
        queue.enqueue(operation_id)

    result = _public_mutation(queue, mutation, operation_id)

    payload = _queue_payload(store)
    assert payload["pending"] == []
    assert result == (operation_id if mutation == "dequeue" else None)
    if state == "completed_pending" and mutation != "remove":
        assert payload["claims"][operation_id]["status"] == "completed"
    else:
        assert operation_id not in payload["claims"]


@pytest.mark.parametrize("mutation", ["discard", "dequeue", "remove"])
@pytest.mark.parametrize("state", ["claimed", "requeued"])
def test_public_queue_mutators_conflict_byte_identically_on_fenced_state(
    mutation: str,
    state: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    queue = RunNowQueue(store)
    operation = _queue_operation(f"op-public-fenced-{state}-{mutation}")
    store.save_operation(operation)
    queue.enqueue(operation.id)
    if state == "claimed":
        assert queue.claim(
            owner_token="fixture-owner",
            lease_epoch=1,
            process_instance_id="public-process",
        ) is not None
    else:
        lease = store.acquire_execution_lease(worker_id="queue-worker")
        claim = queue.claim_from_lease(lease)
        assert claim is not None
        queue.defer_claim_from_lease(
            operation.id,
            claim,
            lease,
            reason="max_concurrent_reached",
        )
    snapshot = (store.root / "queue" / "run_now.json").read_bytes()

    with pytest.raises(RExecOpConcurrencyConflict):
        _public_mutation(queue, mutation, operation.id)

    assert (store.root / "queue" / "run_now.json").read_bytes() == snapshot


@pytest.mark.parametrize("mutation", ["discard", "dequeue", "remove"])
@pytest.mark.parametrize(
    "state",
    [
        "claimed_pending_legacy",
        "invalid_requeued_without_pending",
        "duplicate_pending",
        "malformed_claim",
        "future_epoch",
    ],
)
def test_public_queue_mutators_fail_closed_byte_identically_on_invalid_state(
    mutation: str,
    state: str,
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    queue = RunNowQueue(store)
    operation_id = f"op-public-invalid-{state}-{mutation}"
    queue.enqueue(operation_id)
    if state != "duplicate_pending":
        assert queue.claim(
            owner_token="fixture-owner",
            lease_epoch=1,
            process_instance_id="public-process",
        ) is not None
    payload = _queue_payload(store)
    if state == "claimed_pending_legacy":
        payload["pending"] = [operation_id]
    elif state == "invalid_requeued_without_pending":
        payload["claims"][operation_id]["status"] = "requeued"
    elif state == "duplicate_pending":
        payload["pending"] = [operation_id, operation_id]
    elif state == "malformed_claim":
        payload["claims"][operation_id]["status"] = "unknown"
    else:
        payload["claims"][operation_id]["lease_epoch"] = 1 << 63
    _write_queue_payload(store, payload)
    snapshot = (store.root / "queue" / "run_now.json").read_bytes()

    with pytest.raises(
        RExecOpValidationError,
        match=f"^{QUEUE_CLAIM_RECOVERY_BLOCKED}$",
    ):
        _public_mutation(queue, mutation, operation_id)

    assert (store.root / "queue" / "run_now.json").read_bytes() == snapshot
