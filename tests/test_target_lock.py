from __future__ import annotations

from pathlib import Path

from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"


def _controller(tmp_path: Path) -> OperationController:
    return OperationController(
        store=FileStore(tmp_path / ".rexecop"),
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
    )


def test_target_lock_blocks_second_apply_on_same_target(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    first = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="restart_zabbix_agent",
        target="vm-zabbix-01",
        mode="apply",
    )
    second = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="restart_zabbix_agent",
        target="vm-zabbix-01",
        mode="apply",
    )
    controller.advance(first.id)
    queued = controller.start(second.id)
    assert queued.state == OperationState.APPROVED.value
    assert queued.metadata["queue"]["reason"] == "target_locked"
    assert controller.runtime.queue.list_pending() == [second.id]


def test_target_lock_released_after_completion(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="restart_zabbix_agent",
        target="vm-zabbix-01",
        mode="apply",
    )
    completed = controller.start(operation.id)
    assert completed.state == OperationState.COMPLETED.value
    assert controller.runtime.target_lock.holder_operation_id(
        operation.environment,
        operation.target,
    ) is None
