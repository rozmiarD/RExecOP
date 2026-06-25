from __future__ import annotations

from pathlib import Path

import pytest

from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def _controller(tmp_path: Path, decision: GovEngineDecisionType) -> OperationController:
    return OperationController(
        store=FileStore(tmp_path / ".rexecop"),
        govengine_adapter=StaticGovEngineAdapter(decision),
    )


def test_apply_blocked_without_allowed_decision(tmp_path: Path) -> None:
    controller = _controller(tmp_path, GovEngineDecisionType.BLOCKED)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    assert operation.state == OperationState.BLOCKED.value
    with pytest.raises(RExecOpValidationError):
        controller.start(operation.id)


def test_approval_required_stops_before_mutation(tmp_path: Path) -> None:
    controller = _controller(tmp_path, GovEngineDecisionType.APPROVAL_REQUIRED)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    assert operation.state == OperationState.WAITING_FOR_APPROVAL.value
    assert not controller.allows_mutating_execution(operation.id)
    with pytest.raises(RExecOpValidationError):
        controller.start(operation.id)


def test_approved_apply_completes_on_static_fixture(tmp_path: Path) -> None:
    controller = _controller(tmp_path, GovEngineDecisionType.ALLOWED)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    completed = controller.start(operation.id)
    assert completed.state == OperationState.COMPLETED.value
    mutation = completed.metadata["shared_state"]["mutation_states"]["apply_change"]
    assert mutation["before_state"]["changed"] is False
    assert mutation["after_state"]["changed"] is True


def test_manual_approval_path_completes(tmp_path: Path) -> None:
    controller = _controller(tmp_path, GovEngineDecisionType.APPROVAL_REQUIRED)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    approved = controller.approve(operation.id, approved_by="oncall")
    assert approved.state == OperationState.APPROVED.value
    assert controller.allows_mutating_execution(operation.id)
    completed = controller.start(operation.id)
    assert completed.state == OperationState.COMPLETED.value
    assert controller.store.load_approval(operation.id)["approved_by"] == "oncall"


def test_before_and_after_state_in_evidence(tmp_path: Path) -> None:
    controller = _controller(tmp_path, GovEngineDecisionType.ALLOWED)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    completed = controller.start(operation.id)
    events = controller.store.list_evidence_events(operation.id)
    change_completed = next(
        event
        for event in events
        if event.get("event_type") == "step_completed"
        and event.get("step_id") == "apply_change"
    )
    payload = change_completed["sanitized_payload"]
    assert payload["output"]["before_state"]["changed"] is False
    assert payload["output"]["after_state"]["changed"] is True
    receipt = controller.export_receipt(completed.id)
    assert receipt["sclite_refs"]["execution_receipt"]["digest"]
