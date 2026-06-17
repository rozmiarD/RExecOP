from __future__ import annotations

from pathlib import Path

from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.runtime import ConnectorDispatcher
from rexecop.escalation.package import build_escalation_package
from rexecop.execution.executor import StepExecutor
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.runtime_ops.monitor import OperationMonitor, parse_timeout_seconds
from rexecop.storage.file_store import FileStore
from rexecop.validation.validator import validate_operation_result
from rexecop.workflow.runner import WorkflowRunner

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"


tecrax_fixture = __import__("pytest").importorskip("tecrax.fixture.mock_runtime")


def test_mock_connector_refuses_mutating_action_in_dry_run() -> None:
    runtime = tecrax_fixture.TecraxFixtureConnectorRuntime()
    response = runtime.invoke(
        ConnectorRequest(connector="proxmox", action="restart", target="vm-1", mode="dry_run")
    )
    assert not response.success
    assert "refused" in response.error


def test_workflow_runner_executes_declared_steps_only() -> None:
    runtime = tecrax_fixture.TecraxFixtureConnectorRuntime()
    executor = StepExecutor(connector_dispatcher=ConnectorDispatcher(runtime))
    steps = [
        {"id": "resolve_inventory", "type": "internal", "action": "environment.resolve_targets"},
        {"id": "query_pbs", "type": "connector", "connector": "pbs", "action": "list_snapshots"},
    ]
    result = WorkflowRunner(executor).run(
        operation_id="op-1",
        target="all_critical_vms",
        mode="dry_run",
        planned_steps=steps,
        correlation_id="corr",
    )
    assert result.success
    assert result.executed_steps == ["resolve_inventory", "query_pbs"]


def test_validator_is_deterministic() -> None:
    from rexecop.profile.loader import load_profile

    loaded = load_profile(PROFILE)
    passed = validate_operation_result(
        intent="check_backup_status",
        shared_state={"correlation": {"all_critical_covered": True, "rows": []}},
        profile=loaded,
    )
    failed = validate_operation_result(
        intent="check_backup_status",
        shared_state={"correlation": {"all_critical_covered": False, "rows": []}},
        profile=loaded,
    )
    assert passed["passed"] is True
    assert failed["passed"] is False


def test_monitor_parses_timeout() -> None:
    assert parse_timeout_seconds("20s") == 20
    status = OperationMonitor().status(
        operation_id="op-1",
        state="running",
        current_step_id="query_pbs",
        step={"timeout": "20s"},
    )
    assert status.timeout_seconds == 20


def test_escalation_package_contains_required_fields(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )
    operation.state = OperationState.FAILED.value
    package = build_escalation_package(operation=operation, store=store, failed_step_id="query_pbs")
    assert package["operation_id"] == operation.id
    assert package["failed_step_id"] == "query_pbs"
    assert package["safe_next_options"]
