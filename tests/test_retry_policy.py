from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
from rexecop.connectors.static_fixture import StaticFixtureRuntime
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


@pytest.fixture(autouse=True)
def _clear_mock_failures() -> Generator[None, None, None]:
    StaticFixtureRuntime.clear_failures()
    yield
    StaticFixtureRuntime.clear_failures()


def _controller(tmp_path: Path) -> OperationController:
    return OperationController(
        store=FileStore(tmp_path / ".rexecop"),
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
    )


def test_auto_retry_transient_connector_error(tmp_path: Path) -> None:
    StaticFixtureRuntime.set_failures(
        "fixture_source",
        "apply_fixture_change",
        count=1,
        error_class="transient_connector_error",
    )
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    completed = controller.start(operation.id)
    assert completed.state == OperationState.COMPLETED.value
    history = [item.to_state for item in completed.history]
    assert OperationState.RETRYING.value in history


def test_policy_denied_not_retried(tmp_path: Path) -> None:
    StaticFixtureRuntime.set_failures(
        "fixture_source",
        "apply_fixture_change",
        count=1,
        error="mutating connector action refused in read-only mode",
        error_class="policy_denied",
    )
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    failed = controller.start(operation.id)
    assert failed.state == OperationState.FAILED.value
    assert OperationState.RETRYING.value not in [item.to_state for item in failed.history]
    with pytest.raises(Exception):
        controller.retry(operation.id)


def test_manual_retry_after_failure(tmp_path: Path) -> None:
    StaticFixtureRuntime.set_failures(
        "fixture_source",
        "apply_fixture_change",
        count=3,
        error_class="transient_connector_error",
    )
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    failed = controller.start(operation.id)
    assert failed.state == OperationState.FAILED.value
    StaticFixtureRuntime.clear_failures()
    retried = controller.retry(operation.id)
    assert retried.state == OperationState.COMPLETED.value
