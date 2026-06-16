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


def test_queue_respects_max_concurrent_operations(tmp_path: Path) -> None:
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
        target="vm-pbs-01",
        mode="apply",
    )
    controller.advance(first.id)
    queued = controller.start(second.id)
    assert queued.metadata["queue"]["reason"] == "max_concurrent_reached"
    assert controller.runtime.queue.list_pending() == [second.id]


def test_process_queue_starts_next_operation(tmp_path: Path) -> None:
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
        target="vm-pbs-01",
        mode="apply",
    )
    controller.advance(first.id)
    controller.start(second.id)
    completed = controller.start(first.id)
    assert completed.state == OperationState.COMPLETED.value
    assert controller.get_operation(second.id).state == OperationState.COMPLETED.value
