from __future__ import annotations

from pathlib import Path

import pytest

from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
from rexecop.connectors.mock_runtime import MockConnectorRuntime
from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"


@pytest.fixture(autouse=True)
def _clear_mock_failures() -> None:
    MockConnectorRuntime.clear_failures()
    yield
    MockConnectorRuntime.clear_failures()


def _controller(tmp_path: Path) -> OperationController:
    return OperationController(
        store=FileStore(tmp_path / ".rexecop"),
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
    )


def test_rollback_executes_defined_steps(tmp_path: Path) -> None:
    MockConnectorRuntime.set_failures(
        "proxmox",
        "restart",
        count=3,
        error_class="transient_connector_error",
    )
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="restart_zabbix_agent",
        target="vm-zabbix-01",
        mode="apply",
    )
    failed = controller.start(operation.id)
    assert failed.state == OperationState.FAILED.value
    result = controller.rollback(operation.id)
    assert result["success"] is True
    assert "rollback_marker" in result["executed_steps"]
    assert controller.get_operation(operation.id).metadata["rollback"]["success"] is True


def test_rollback_rejected_without_workflow_block(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )
    controller.start(operation.id)
    with pytest.raises(RExecOpValidationError):
        controller.rollback(operation.id)
