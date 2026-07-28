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
from rexecop.operation.state import OperationState
from rexecop.runtime_ops.queue import QUEUE_CLAIM_RECOVERY_BLOCKED, RunNowQueue
from rexecop.storage.file_store import FileStore
from runtime_governance_support import governance_runtime_kwargs

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def _controller(tmp_path: Path) -> OperationController:
    return OperationController(
        store=FileStore(tmp_path / ".rexecop"),
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
        **governance_runtime_kwargs(),
    )


def test_target_lock_blocks_second_apply_on_same_target(
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
        target="fixture-target",
        mode="apply",
    )
    controller.advance(first.id)
    queued = controller.start(second.id)
    assert queued.state == OperationState.APPROVED.value
    assert queued.metadata["queue"]["reason"] == "target_locked"
    assert controller.runtime.queue.list_pending() == [second.id]
    lock_files = list((controller.store.root / "locks").glob("*.lock"))
    assert lock_files
    assert lock_files[0].stat().st_mode & 0o777 == 0o600
    assert lock_files[0].parent.stat().st_mode & 0o777 == 0o700


def test_target_lock_released_after_completion(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    completed = controller.start(operation.id)
    assert completed.state == OperationState.COMPLETED.value
    assert (
        controller.runtime.target_lock.holder_operation_id(
            operation.environment,
            operation.target,
        )
        is None
    )


def test_repeated_target_lock_defer_then_release_executes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    controller = _controller(tmp_path)
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
        target="fixture-target",
        mode="apply",
    )
    controller.advance(blocker.id)
    queued = controller.start(deferred.id)
    assert queued.metadata["queue"]["reason"] == "target_locked"
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
    assert first_claim["last_transition"]["reason"] == "target_locked"
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

    assert controller.start(blocker.id).state == OperationState.COMPLETED.value
    assert controller.get_operation(deferred.id).state == OperationState.COMPLETED.value
    assert invocations == ["apply_fixture_change", "apply_fixture_change"]
    attempts = controller.store.list_execution_attempts(deferred.id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "completed"
    assert controller.process_queue() == []
    assert invocations == ["apply_fixture_change", "apply_fixture_change"]


def test_direct_start_final_target_lock_race_defers_then_executes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
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
    invocations: list[str] = []
    assessments: list[tuple[str, str]] = []
    acquire_count = 0
    invoke = StaticFixtureRuntime.invoke
    assess = controller.runtime._assess_for_execution
    acquire = controller.runtime.target_lock.try_acquire

    def record_invoke(runtime: StaticFixtureRuntime, request: Any) -> Any:
        invocations.append(request.action)
        return invoke(runtime, request)

    def record_assessment(candidate: Any) -> tuple[str, str]:
        result = assess(candidate)
        assessments.append(result)
        return result

    def race_once(**kwargs: Any) -> bool:
        nonlocal acquire_count
        acquire_count += 1
        if acquire_count == 1:
            return False
        return acquire(**kwargs)

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)
    monkeypatch.setattr(controller.runtime, "_assess_for_execution", record_assessment)
    monkeypatch.setattr(controller.runtime.target_lock, "try_acquire", race_once)

    queued = controller.start(operation.id)
    assert queued.state == OperationState.APPROVED.value
    assert queued.metadata["queue"]["reason"] == "target_locked"
    deferred = json.loads(
        (controller.store.root / "queue" / "run_now.json").read_text(
            encoding="utf-8"
        )
    )
    first_claim = deferred["claims"][operation.id]
    assert deferred["pending"] == [operation.id]
    assert first_claim["status"] == "requeued"
    assert first_claim["last_transition"]["reason"] == "target_locked"
    assert invocations == []

    assert controller.start(operation.id).state == OperationState.COMPLETED.value
    completed = json.loads(
        (controller.store.root / "queue" / "run_now.json").read_text(
            encoding="utf-8"
        )
    )
    assert completed["pending"] == []
    assert completed["claims"][operation.id]["status"] == "completed"
    assert completed["claims"][operation.id]["attempt"] == first_claim["attempt"] + 1

    assert assessments == [("admitted", ""), ("admitted", "")]
    assert acquire_count == 2
    assert invocations == ["apply_fixture_change"]
    attempts = controller.store.list_execution_attempts(operation.id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "completed"


@pytest.mark.parametrize("error_kind", ["concurrency", "sentinel"])
def test_post_acquire_persistence_error_propagates_without_target_lock_defer(
    error_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
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
    expected: Exception = (
        RExecOpConcurrencyConflict("persistence-conflict")
        if error_kind == "concurrency"
        else RuntimeError("persistence-sentinel")
    )
    invocations: list[str] = []

    def fail_persistence(_operation: Any) -> None:
        raise expected

    def reject_invoke(_runtime: StaticFixtureRuntime, _request: Any) -> Any:
        invocations.append("unexpected")
        raise AssertionError("connector I/O must remain unreachable")

    monkeypatch.setattr(
        controller.runtime,
        "_persist_prechecked_admission",
        fail_persistence,
    )
    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

    with controller.execution_lease():
        with pytest.raises(type(expected)) as caught:
            controller._drain_queue()

    assert caught.value is expected
    payload = json.loads(
        (controller.store.root / "queue" / "run_now.json").read_text(
            encoding="utf-8"
        )
    )
    claim = payload["claims"][operation.id]
    assert payload["pending"] == []
    assert claim["status"] == "claimed"
    assert claim.get("last_transition", {}).get("reason") != "target_locked"
    assert controller.get_operation(operation.id).metadata["queue"]["reason"] == (
        "fixture-queued"
    )
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) == operation.id
    assert invocations == []


@pytest.mark.parametrize("queue_state", ["empty", "bare"])
def test_public_release_removes_compatible_queue_before_target_lock(
    queue_state: str,
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
    if queue_state == "bare":
        controller.runtime._mark_queued(operation, reason="fixture-queued")
    assert controller.runtime.target_lock.try_acquire(
        environment=operation.environment,
        target=operation.target,
        operation_id=operation.id,
    )
    order: list[str] = []
    discard = controller.runtime.queue.discard_pending
    release = controller.runtime._release_target_only

    def ordered_discard(operation_id: str) -> None:
        discard(operation_id)
        order.append("queue_removed")

    def ordered_release(candidate: Any) -> None:
        assert order == ["queue_removed"]
        release(candidate)
        order.append("target_released")

    monkeypatch.setattr(
        controller.runtime.queue,
        "discard_pending",
        ordered_discard,
    )
    monkeypatch.setattr(controller.runtime, "_release_target_only", ordered_release)

    controller.runtime.release_operation(operation)

    assert order == ["queue_removed", "target_released"]
    assert controller.runtime.queue.list_pending() == []
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) is None


@pytest.mark.parametrize(
    ("queue_state", "expected_error"),
    [
        ("claimed", RExecOpConcurrencyConflict),
        ("requeued", RExecOpConcurrencyConflict),
        ("invalid", RExecOpValidationError),
    ],
)
def test_public_release_fails_before_target_on_fenced_or_invalid_queue(
    queue_state: str,
    expected_error: type[Exception],
    tmp_path: Path,
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
    assert controller.runtime.target_lock.try_acquire(
        environment=operation.environment,
        target=operation.target,
        operation_id=operation.id,
    )
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
                reason="target_locked",
            )
    else:
        queue_path = controller.store.root / "queue" / "run_now.json"
        payload = json.loads(queue_path.read_text(encoding="utf-8"))
        payload["pending"].append(operation.id)
        queue_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    queue_path = controller.store.root / "queue" / "run_now.json"
    lock_path = next((controller.store.root / "locks").glob("*.lock"))
    queue_snapshot = queue_path.read_bytes()
    lock_snapshot = lock_path.read_bytes()

    with pytest.raises(expected_error) as caught:
        controller.runtime.release_operation(operation)

    if queue_state == "invalid":
        assert str(caught.value) == QUEUE_CLAIM_RECOVERY_BLOCKED
    assert queue_path.read_bytes() == queue_snapshot
    assert lock_path.read_bytes() == lock_snapshot
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) == operation.id
