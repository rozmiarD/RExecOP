from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path

import pytest

from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.static_fixture import StaticFixtureRuntime
from rexecop.errors import RExecOpOutcomeIndeterminate
from rexecop.execution.backend import StepExecutionResult
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore
from runtime_governance_support import governance_runtime_kwargs

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
        **governance_runtime_kwargs(),
    )


def test_auto_retry_transient_connector_error(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
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
    attempt_paths = list((controller.store.root / "attempts" / operation.id).glob("*.json"))
    attempts = [json.loads(path.read_text(encoding="utf-8")) for path in attempt_paths]
    assert len(attempts) == 2
    assert sorted(item["status"] for item in attempts) == ["completed", "failed"]
    completed_attempt = next(item for item in attempts if item["status"] == "completed")
    assert completed_attempt["result_digest"].startswith("sha256:")


def test_policy_denied_not_retried(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
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


def test_manual_retry_after_failure(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
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


def test_governed_mutation_receipt_failure_is_durable_indeterminate_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    connector_calls: list[str] = []
    original_invoke = StaticFixtureRuntime.invoke

    def tracked_invoke(
        runtime: StaticFixtureRuntime,
        request: ConnectorRequest,
    ):
        connector_calls.append(request.action)
        return original_invoke(runtime, request)

    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    plan = controller.store.load_plan(operation.id)
    plan.retry_policy_summary = {
        "max_attempts": 4,
        "allowed_on": [],
        "blocked_on": [],
    }
    controller.store.save_plan(plan)

    def reject_receipt(
        _attempt: dict[str, object],
        result: StepExecutionResult,
    ) -> StepExecutionResult:
        output = dict(result.output)
        output["error_class"] = "receipt_postcondition_failed"
        output["receipt_reason_code"] = "receipt_output_limit_exceeded"
        return replace(
            result,
            success=False,
            output=output,
            error="runtime receipt postcondition failed",
        )

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", tracked_invoke)
    monkeypatch.setattr(controller.orchestrator, "_bind_attempt_receipt", reject_receipt)

    failed = controller.start(operation.id)

    assert failed.state == OperationState.FAILED.value
    assert connector_calls == ["apply_fixture_change"]
    assert OperationState.RETRYING.value not in [item.to_state for item in failed.history]
    assert failed.metadata["last_failure"]["error_class"] == "outcome_indeterminate"
    step_result = failed.metadata["step_results"]["apply_change"]
    assert step_result["output"]["error_class"] == "outcome_indeterminate"
    assert step_result["output"]["receipt_reason_code"] == (
        "receipt_output_limit_exceeded"
    )

    attempt_paths = list((controller.store.root / "attempts" / operation.id).glob("*.json"))
    assert len(attempt_paths) == 1
    attempt = json.loads(attempt_paths[0].read_text(encoding="utf-8"))
    assert attempt["status"] == "indeterminate"
    assert attempt["error_class"] == "outcome_indeterminate"
    assert attempt["result_digest"].startswith("sha256:")

    with pytest.raises(RExecOpOutcomeIndeterminate):
        controller.retry(operation.id)


@pytest.mark.parametrize("allowed_on", [[], ["outcome_indeterminate"]])
def test_outcome_indeterminate_is_intrinsically_non_retryable(
    tmp_path: Path,
    allowed_on: list[str],
) -> None:
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    plan = controller.store.load_plan(operation.id)
    plan.retry_policy_summary = {
        "max_attempts": 99,
        "allowed_on": allowed_on,
        "blocked_on": [],
    }

    assert (
        controller.orchestrator._error_retryable(  # noqa: SLF001 - direct classifier contract
            plan,
            error_class="outcome_indeterminate",
        )
        is False
    )


def test_read_only_transient_connector_failure_still_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    StaticFixtureRuntime.set_failures(
        "fixture_source",
        "read_fixture_state",
        count=1,
        error_class="transient_connector_error",
    )
    connector_calls: list[str] = []
    original_invoke = StaticFixtureRuntime.invoke

    def tracked_invoke(
        runtime: StaticFixtureRuntime,
        request: ConnectorRequest,
    ):
        connector_calls.append(request.action)
        return original_invoke(runtime, request)

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", tracked_invoke)
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )

    completed = controller.start(operation.id)

    assert completed.state == OperationState.COMPLETED.value
    assert connector_calls == ["read_fixture_state", "read_fixture_state"]
    assert OperationState.RETRYING.value in [item.to_state for item in completed.history]
    attempt_paths = list((controller.store.root / "attempts" / operation.id).glob("*.json"))
    attempts = [json.loads(path.read_text(encoding="utf-8")) for path in attempt_paths]
    assert len(attempts) == 2
    assert sorted(item["status"] for item in attempts) == ["completed", "failed"]
