from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.connectors.http_support import validate_destination_posture
from rexecop.errors import (
    RExecOpConcurrencyConflict,
    RExecOpLeaseLost,
    RExecOpOutcomeIndeterminate,
    RExecOpUnsafeDestination,
)
from rexecop.execution.backend import StepExecutionContext
from rexecop.execution.executor import StepExecutor
from rexecop.operation.controller import OperationController
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"
runner = CliRunner()


def test_runtime_reason_codes_are_typed_and_stable() -> None:
    assert RExecOpUnsafeDestination.reason_code == "unsafe_destination"
    assert RExecOpConcurrencyConflict.reason_code == "concurrency_conflict"
    assert RExecOpLeaseLost.reason_code == "lease_lost"
    assert RExecOpOutcomeIndeterminate.reason_code == "outcome_indeterminate"


def test_unsafe_destination_uses_reason_code_not_message_parsing() -> None:
    with pytest.raises(RExecOpUnsafeDestination) as caught:
        validate_destination_posture({}, "http://example.invalid")

    assert caught.value.reason_code == "unsafe_destination"


def test_executor_hides_unknown_exception_text_and_traceback() -> None:
    sensitive = "private-backend-detail-should-not-leak"

    def explode(_context):
        raise RuntimeError(sensitive)

    result = StepExecutor(internal_handlers={"explode": explode}).execute(
        StepExecutionContext(
            operation_id="op-error",
            target="target",
            mode="dry_run",
            step={"id": "explode", "type": "internal", "action": "explode"},
            shared_state={},
        )
    )

    assert result.success is False
    assert result.output["reason_code"] == "internal_error"
    assert sensitive not in json.dumps(result.as_dict())
    assert "Traceback" not in json.dumps(result.as_dict())


def test_retry_cli_emits_outcome_indeterminate_without_internal_message(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".rexecop"
    store = FileStore(root)
    controller = OperationController(store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    plan = store.load_plan(operation.id)
    attempt = store.start_execution_attempt(
        operation_id=operation.id,
        operation_revision=operation.operation_revision,
        step_id="effect",
        plan=plan.as_dict(),
        execution_spec={"digest": "sha256:" + "d" * 64},
        target=operation.target,
        mode="apply",
        lease={"lease_epoch": 1, "process_instance_id": "lost"},
    )
    store.finish_execution_attempt(attempt, status="indeterminate")

    result = runner.invoke(
        app,
        ["--root", str(root), "--json", "retry", "--operation", operation.id],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["reason_code"] == "outcome_indeterminate"
    assert payload["message"] == RExecOpOutcomeIndeterminate.public_message
    assert "side-effectful" not in result.stdout
