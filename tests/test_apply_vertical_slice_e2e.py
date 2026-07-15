from __future__ import annotations

from pathlib import Path

import pytest

from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore
from runtime_governance_support import governance_runtime_kwargs

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def _controller(tmp_path: Path, decision: GovEngineDecisionType) -> OperationController:
    return OperationController(
        store=FileStore(tmp_path / ".rexecop"),
        govengine_adapter=StaticGovEngineAdapter(decision),
        **governance_runtime_kwargs(),
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


def test_operation_admission_is_not_mutation_approval(tmp_path: Path) -> None:
    controller = _controller(tmp_path, GovEngineDecisionType.ALLOWED)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    completed = controller.start(operation.id)
    assert completed.state == OperationState.FAILED.value
    shared = completed.metadata["shared_state"]
    assert shared["typed_execution_admissions"]["apply_change"]["reason_code"] == (
        "mutation_requires_approval_attestation"
    )
    assert "mutation_states" not in shared


def test_legacy_manual_approval_is_not_bound_attestation(tmp_path: Path) -> None:
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
    assert completed.state == OperationState.FAILED.value
    shared = completed.metadata["shared_state"]
    assert shared["typed_execution_admissions"]["apply_change"]["reason_code"] == (
        "mutation_requires_approval_attestation"
    )
    assert "mutation_states" not in shared
    assert controller.store.load_approval(operation.id)["approved_by"] == "oncall"


def test_before_and_after_state_in_evidence(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
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
        if event.get("event_type") == "step_completed" and event.get("step_id") == "apply_change"
    )
    payload = change_completed["sanitized_payload"]
    assert payload["output"]["before_state"]["projection"] == "digest_only"
    assert payload["output"]["after_state"]["projection"] == "digest_only"
    receipt = controller.export_receipt(completed.id)
    assert receipt["sclite_refs"]["execution_receipt"]["digest"]
