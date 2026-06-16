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
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"


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
        intent="restart_zabbix_agent",
        target="vm-zabbix-01",
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
        intent="restart_zabbix_agent",
        target="vm-zabbix-01",
        mode="apply",
    )
    assert operation.state == OperationState.WAITING_FOR_APPROVAL.value
    assert not controller.allows_mutating_execution(operation.id)
    with pytest.raises(RExecOpValidationError):
        controller.start(operation.id)


def test_approved_apply_completes_on_mock_connector(tmp_path: Path) -> None:
    controller = _controller(tmp_path, GovEngineDecisionType.ALLOWED)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="restart_zabbix_agent",
        target="vm-zabbix-01",
        mode="apply",
    )
    completed = controller.start(operation.id)
    assert completed.state == OperationState.COMPLETED.value
    mutation = completed.metadata["shared_state"]["mutation_states"]["restart_agent"]
    assert mutation["before_state"]["agent_status"] == "running"
    assert mutation["after_state"]["agent_status"] == "restarted"


def test_manual_approval_path_completes(tmp_path: Path) -> None:
    controller = _controller(tmp_path, GovEngineDecisionType.APPROVAL_REQUIRED)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="restart_zabbix_agent",
        target="vm-zabbix-01",
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
        intent="restart_zabbix_agent",
        target="vm-zabbix-01",
        mode="apply",
    )
    completed = controller.start(operation.id)
    events = controller.store.list_evidence_events(operation.id)
    restart_completed = next(
        event
        for event in events
        if event.get("event_type") == "step_completed"
        and event.get("step_id") == "restart_agent"
    )
    payload = restart_completed["sanitized_payload"]
    assert payload["output"]["before_state"]["agent_status"] == "running"
    assert payload["output"]["after_state"]["agent_status"] == "restarted"
    receipt = controller.export_receipt(completed.id)
    assert receipt["sclite_refs"]["execution_receipt"]["digest"]
