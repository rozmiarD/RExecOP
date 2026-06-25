from __future__ import annotations

from pathlib import Path

import pytest

from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.mock_runtime import MockConnectorRuntime
from rexecop.connectors.runtime import ConnectorDispatcher
from rexecop.connectors.static_fixture import StaticFixtureRuntime
from rexecop.errors import RExecOpValidationError
from rexecop.execution.backend import StepExecutionContext
from rexecop.execution.executor import StepExecutor
from rexecop.execution.internal_registry import load_internal_handlers
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore


def test_generic_mock_rejects_unknown_actions() -> None:
    runtime = MockConnectorRuntime()
    response = runtime.invoke(
        ConnectorRequest(
            connector="source",
            action="read_state",
            target="fixture-target",
            mode="dry_run",
        )
    )
    assert not response.success
    assert "unsupported mock" in response.error


def test_static_fixture_returns_configured_payload() -> None:
    runtime = StaticFixtureRuntime(
        connector_name="fixture_source",
        config={
            "fixture_only": True,
            "actions": {"read_fixture_state": {"data": {"status": "ready"}}},
        },
        mutating_allowed=False,
    )
    response = runtime.invoke(
        ConnectorRequest(
            connector="fixture_source",
            action="read_fixture_state",
            target="fixture-target",
            mode="dry_run",
        )
    )
    assert response.success
    assert response.data == {"status": "ready"}


def test_dispatcher_uses_injected_runtime() -> None:
    runtime = StaticFixtureRuntime(
        connector_name="fixture_source",
        config={
            "fixture_only": True,
            "actions": {"read_fixture_state": {"data": {"status": "ready"}}},
        },
        mutating_allowed=False,
    )
    dispatcher = ConnectorDispatcher(runtime)
    response = dispatcher.invoke(
        ConnectorRequest(
            connector="fixture_source",
            action="read_fixture_state",
            target="t",
            mode="dry_run",
        )
    )
    assert response.success


def test_internal_action_registry_loads_builtin_handlers() -> None:
    handlers = load_internal_handlers()
    assert "record_execution_checkpoint" in handlers
    assert "record_rollback_marker" in handlers


def test_unregistered_internal_action_error() -> None:
    executor = StepExecutor(internal_handlers={})
    context = StepExecutionContext(
        operation_id="op-1",
        target="t",
        mode="dry_run",
        step={"id": "s1", "type": "internal", "action": "missing.action"},
        shared_state={},
    )
    result = executor.execute(context)
    assert not result.success
    assert result.error == "internal_action_not_registered:missing.action"


REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def test_escalate_from_failed_operation(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
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
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    with pytest.raises(RExecOpValidationError):
        controller.escalate(operation.id)
