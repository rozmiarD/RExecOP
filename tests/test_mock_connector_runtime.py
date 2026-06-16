from __future__ import annotations

from pathlib import Path

import pytest

from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.mock_runtime import MockConnectorRuntime
from rexecop.connectors.runtime import ConnectorDispatcher
from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"


def test_mock_runtime_returns_fixture_payloads() -> None:
    runtime = MockConnectorRuntime()
    proxmox = runtime.invoke(
        ConnectorRequest(
            connector="proxmox",
            action="list_vms",
            target="all_critical_vms",
            mode="dry_run",
        )
    )
    pbs = runtime.invoke(
        ConnectorRequest(
            connector="pbs",
            action="list_snapshots",
            target="all_critical_vms",
            mode="dry_run",
        )
    )
    assert proxmox.success and proxmox.data["vms"]
    assert pbs.success and pbs.data["snapshots"]


def test_dispatcher_uses_runtime() -> None:
    dispatcher = ConnectorDispatcher(MockConnectorRuntime())
    response = dispatcher.invoke(
        ConnectorRequest(connector="pbs", action="list_snapshots", target="t", mode="dry_run")
    )
    assert response.success


def test_escalate_from_failed_operation(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )
    failed = controller.get_operation(operation.id)
    failed.state = OperationState.FAILED.value
    store.save_operation(failed)

    package = controller.escalate(operation.id)
    assert package["operation_id"] == operation.id
    escalated = controller.get_operation(operation.id)
    assert escalated.state == OperationState.ESCALATED.value


def test_escalate_rejects_non_failed_state(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )
    with pytest.raises(RExecOpValidationError):
        controller.escalate(operation.id)
