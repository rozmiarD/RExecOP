from __future__ import annotations

from pathlib import Path

from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.runtime import ConnectorDispatcher
from rexecop.connectors.static_fixture import StaticFixtureRuntime
from rexecop.escalation.package import build_escalation_package
from rexecop.execution.executor import StepExecutor
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.runtime_ops.monitor import OperationMonitor, parse_timeout_seconds
from rexecop.storage.file_store import FileStore
from rexecop.validation.validator import validate_operation_result
from rexecop.workflow.runner import WorkflowRunner

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def _fixture_runtime(*, mutating_allowed: bool = False) -> StaticFixtureRuntime:
    return StaticFixtureRuntime(
        connector_name="fixture_source",
        mutating_allowed=mutating_allowed,
        config={
            "fixture_only": True,
            "actions": {
                "read_fixture_state": {"data": {"observed": True}},
                "apply_fixture_change": {
                    "mutating": True,
                    "data": {
                        "before_state": {"changed": False},
                        "after_state": {"changed": True},
                    },
                },
            },
        },
    )


def test_mock_connector_refuses_mutating_action_in_dry_run() -> None:
    runtime = _fixture_runtime(mutating_allowed=True)
    response = runtime.invoke(
        ConnectorRequest(
            connector="fixture_source",
            action="apply_fixture_change",
            target="fixture-target",
            mode="dry_run",
        )
    )
    assert not response.success
    assert "refused" in response.error


def test_workflow_runner_executes_declared_steps_only() -> None:
    runtime = _fixture_runtime()
    executor = StepExecutor(connector_dispatcher=ConnectorDispatcher(runtime))
    steps = [
        {
            "id": "checkpoint",
            "type": "internal",
            "action": "record_execution_checkpoint",
        },
        {
            "id": "inspect_state",
            "type": "connector",
            "connector": "fixture_source",
            "action": "read_fixture_state",
        },
    ]
    result = WorkflowRunner(executor).run(
        operation_id="op-1",
        target="fixture-target",
        mode="dry_run",
        planned_steps=steps,
        correlation_id="corr",
    )
    assert result.success
    assert result.executed_steps == ["checkpoint", "inspect_state"]
    assert result.shared_state["execution_request"]["source"] == "approved_workflow_plan"
    assert result.shared_state["execution_receipt"]["success"] is True
    assert result.shared_state["execution_receipt"]["executed_steps"] == [
        "checkpoint",
        "inspect_state",
    ]


def test_readonly_diagnostic_continues_after_declared_connector_failure() -> None:
    runtime = _fixture_runtime()
    executor = StepExecutor(
        connector_dispatcher=ConnectorDispatcher(runtime),
        internal_handlers={"record": lambda context: {"recorded": True}},
    )
    steps = [
        {
            "id": "optional_probe",
            "type": "connector",
            "connector": "missing",
            "action": "probe",
            "metadata": {"continue_on_error": True},
        },
        {"id": "aggregate", "type": "internal", "action": "record"},
    ]

    result = WorkflowRunner(executor).run(
        operation_id="op-diagnostic",
        target="host",
        mode="dry_run",
        planned_steps=steps,
        correlation_id="corr",
    )

    assert result.success is True
    assert result.executed_steps == ["aggregate"]
    assert result.step_results["optional_probe"]["success"] is False
    assert result.shared_state["continued_failures"]["optional_probe"]["error"]
    assert result.shared_state["execution_receipt"]["success"] is True


def test_continue_on_error_does_not_apply_to_mutating_mode() -> None:
    runtime = _fixture_runtime()
    executor = StepExecutor(connector_dispatcher=ConnectorDispatcher(runtime))
    result = WorkflowRunner(executor).run(
        operation_id="op-apply",
        target="host",
        mode="apply",
        planned_steps=[
            {
                "id": "optional_probe",
                "type": "connector",
                "connector": "missing",
                "action": "probe",
                "metadata": {"continue_on_error": True},
            }
        ],
        correlation_id="corr",
    )

    assert result.success is False
    assert "continued_failures" not in result.shared_state


def test_continued_failure_metadata_is_bounded() -> None:
    runtime = _fixture_runtime()
    executor = StepExecutor(connector_dispatcher=ConnectorDispatcher(runtime))
    result = WorkflowRunner(executor).run(
        operation_id="op-bounded",
        target="host",
        mode="dry_run",
        planned_steps=[
            {
                "id": "optional_probe",
                "type": "connector",
                "connector": "missing",
                "action": "x" * 2048,
                "metadata": {"continue_on_error": True},
            }
        ],
        correlation_id="corr",
    )

    failure = result.shared_state["continued_failures"]["optional_probe"]
    assert len(failure["error"]) <= 512
    assert len(failure["error_class"]) <= 64


def test_validator_is_deterministic() -> None:
    from rexecop.profile.loader import load_profile

    loaded = load_profile(PROFILE)
    passed = validate_operation_result(
        intent="inspect_fixture_state",
        shared_state={
            "connector_results": {"inspect_state": {"observed": True}}
        },
        profile=loaded,
    )
    failed = validate_operation_result(
        intent="inspect_fixture_state",
        shared_state={
            "connector_results": {"inspect_state": {"observed": False}}
        },
        profile=loaded,
    )
    assert passed["passed"] is True
    assert failed["passed"] is False


def test_monitor_parses_timeout() -> None:
    assert parse_timeout_seconds("20s") == 20
    status = OperationMonitor().status(
        operation_id="op-1",
        state="running",
        current_step_id="inspect_state",
        step={"timeout": "20s"},
    )
    assert status.timeout_seconds == 20


def test_escalation_package_contains_required_fields(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    operation.state = OperationState.FAILED.value
    package = build_escalation_package(
        operation=operation, store=store, failed_step_id="inspect_state"
    )
    assert package["operation_id"] == operation.id
    assert package["failed_step_id"] == "inspect_state"
    assert package["safe_next_options"]
