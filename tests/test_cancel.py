from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
from rexecop.connectors.static_fixture import StaticFixtureRuntime
from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.operation.model import Operation
from rexecop.operation.state import ALLOWED_TRANSITIONS, OperationState
from rexecop.storage.file_store import FileStore
from runtime_governance_support import governance_runtime_kwargs

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"
CANCEL_SOURCE_STATES = frozenset(
    {
        OperationState.WAITING_FOR_APPROVAL,
        OperationState.APPROVED,
        OperationState.RUNNING,
        OperationState.PAUSED,
    }
)


def _controller(
    tmp_path: Path,
    decision: GovEngineDecisionType,
) -> OperationController:
    return OperationController(
        store=FileStore(tmp_path / ".rexecop"),
        govengine_adapter=StaticGovEngineAdapter(decision),
        **governance_runtime_kwargs(),
    )


def _plan_apply(controller: OperationController) -> Operation:
    return controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )


def _operation_has_no_queue_membership(
    store: FileStore,
    operation_id: str,
) -> bool:
    queue_path = store.root / "queue" / "run_now.json"
    if not queue_path.is_file():
        return True
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    return (
        operation_id not in payload.get("pending", [])
        and operation_id not in payload.get("claims", {})
    )


def _prepare_source_state(
    controller: OperationController,
    source_state: OperationState,
) -> Operation:
    operation = _plan_apply(controller)
    if source_state is OperationState.APPROVED:
        controller.runtime._mark_queued(operation, reason="fixture-queued")
    elif source_state in {OperationState.RUNNING, OperationState.PAUSED}:
        operation = controller.advance(operation.id, max_steps=1)
        if source_state is OperationState.PAUSED:
            operation = controller.pause(operation.id)
    assert operation.state == source_state.value
    return operation


@pytest.mark.parametrize(
    "source_state",
    [
        OperationState.WAITING_FOR_APPROVAL,
        OperationState.APPROVED,
        OperationState.RUNNING,
        OperationState.PAUSED,
    ],
)
def test_public_cancel_cleans_each_advertised_source_state_without_connector_io(
    source_state: OperationState,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_lab_mutation_runtime_test: None,
) -> None:
    decision = (
        GovEngineDecisionType.APPROVAL_REQUIRED
        if source_state is OperationState.WAITING_FOR_APPROVAL
        else GovEngineDecisionType.ALLOWED
    )
    controller = _controller(tmp_path, decision)
    operation = _prepare_source_state(controller, source_state)
    if source_state is OperationState.APPROVED:
        assert operation.id in controller.store.queue_list_pending()
    if source_state in {OperationState.RUNNING, OperationState.PAUSED}:
        assert controller.runtime.target_lock.holder_operation_id(
            operation.environment,
            operation.target,
        ) == operation.id
    attempts_before = controller.store.list_execution_attempts(operation.id)
    invocations: list[str] = []

    def reject_invoke(
        _runtime: StaticFixtureRuntime,
        request: Any,
    ) -> Any:
        invocations.append(request.action)
        raise AssertionError("cancel must not invoke a connector")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

    cancelled = controller.cancel(operation.id)
    durable = controller.store.load_operation(operation.id)

    assert cancelled.state == OperationState.CANCELLED.value
    assert durable.state == OperationState.CANCELLED.value
    assert durable.history[-1].from_state == source_state.value
    assert durable.history[-1].to_state == OperationState.CANCELLED.value
    assert durable.history[-1].reason == "operator_cancel"
    transition_events = [
        event
        for event in controller.store.list_evidence_events(operation.id)
        if event["event_type"] == "state_transition"
        and event["state_after"] == OperationState.CANCELLED.value
    ]
    assert len(transition_events) == 1
    assert transition_events[0]["state_before"] == source_state.value
    assert transition_events[0]["sanitized_payload"] == {
        "reason": "operator_cancel"
    }
    assert _operation_has_no_queue_membership(controller.store, operation.id)
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) is None
    assert controller.store.list_execution_attempts(operation.id) == attempts_before
    assert invocations == []


def test_canonical_cancel_source_set_is_exact() -> None:
    actual = frozenset(
        source
        for source, targets in ALLOWED_TRANSITIONS.items()
        if OperationState.CANCELLED in targets
    )

    assert actual == CANCEL_SOURCE_STATES


def test_public_cancel_rejects_representative_unsupported_nonterminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    assert operation.state == OperationState.PLANNED.value
    attempts_before = controller.store.list_execution_attempts(operation.id)

    def reject_invoke(
        _runtime: StaticFixtureRuntime,
        _request: Any,
    ) -> Any:
        raise AssertionError("rejected cancel must not invoke a connector")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

    with pytest.raises(
        RExecOpValidationError,
        match="^operation cannot be cancelled from state: planned$",
    ):
        controller.cancel(operation.id)

    durable = controller.store.load_operation(operation.id)
    assert durable.state == OperationState.PLANNED.value
    assert not any(
        event["event_type"] == "state_transition"
        and event["state_after"] == OperationState.CANCELLED.value
        for event in controller.store.list_evidence_events(operation.id)
    )
    assert _operation_has_no_queue_membership(controller.store, operation.id)
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) is None
    assert controller.store.list_execution_attempts(operation.id) == attempts_before


def test_repeated_public_cancel_is_idempotent_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_lab_mutation_runtime_test: None,
) -> None:
    controller = _controller(tmp_path, GovEngineDecisionType.ALLOWED)
    operation = _prepare_source_state(controller, OperationState.RUNNING)
    attempts_before = controller.store.list_execution_attempts(operation.id)

    def reject_invoke(
        _runtime: StaticFixtureRuntime,
        _request: Any,
    ) -> Any:
        raise AssertionError("cancel cleanup must not invoke a connector")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", reject_invoke)

    first = controller.cancel(operation.id)
    history_after_first = list(first.history)
    evidence_after_first = list(first.evidence_event_ids)
    second = controller.cancel(operation.id)

    assert second.state == OperationState.CANCELLED.value
    assert second.history == history_after_first
    assert second.evidence_event_ids == evidence_after_first
    assert _operation_has_no_queue_membership(controller.store, operation.id)
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) is None
    assert controller.store.list_execution_attempts(operation.id) == attempts_before
