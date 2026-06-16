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


def test_pause_only_at_pause_safe_step(tmp_path: Path) -> None:
    controller = _controller(tmp_path, GovEngineDecisionType.ALLOWED)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="restart_zabbix_agent",
        target="vm-zabbix-01",
        mode="apply",
    )
    running = controller.advance(operation.id)
    assert running.state == OperationState.RUNNING.value
    assert running.current_step_id == "capture_state"

    paused = controller.pause(operation.id)
    assert paused.state == OperationState.PAUSED.value

    with pytest.raises(RExecOpValidationError):
        controller.pause(operation.id)


def test_resume_continues_apply_workflow(tmp_path: Path) -> None:
    controller = _controller(tmp_path, GovEngineDecisionType.ALLOWED)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="restart_zabbix_agent",
        target="vm-zabbix-01",
        mode="apply",
    )
    controller.advance(operation.id)
    controller.pause(operation.id)
    completed = controller.resume(operation.id)
    assert completed.state == OperationState.COMPLETED.value


def test_pause_rejected_on_non_pause_safe_step(tmp_path: Path) -> None:
    controller = _controller(tmp_path, GovEngineDecisionType.ALLOWED)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="restart_zabbix_agent",
        target="vm-zabbix-01",
        mode="apply",
    )
    controller.advance(operation.id)
    controller.advance(operation.id)
    running = controller.get_operation(operation.id)
    assert running.current_step_id == "restart_agent"
    with pytest.raises(RExecOpValidationError):
        controller.pause(operation.id)
