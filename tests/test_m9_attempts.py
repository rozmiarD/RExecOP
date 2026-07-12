from __future__ import annotations

import json
from pathlib import Path

import pytest

from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.runtime_ops.attempts import AttemptJournal
from rexecop.runtime_ops.recovery import run_startup_recovery
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def test_connector_io_has_durable_completed_attempt_binding(tmp_path: Path) -> None:
    controller = OperationController(FileStore(tmp_path / ".rexecop"))
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )

    completed = controller.start(operation.id)

    attempts = sorted((controller.store.root / "attempts" / operation.id).glob("*.json"))
    assert attempts
    records = [json.loads(path.read_text(encoding="utf-8")) for path in attempts]
    connector_attempt = next(item for item in records if item["step_id"] == "inspect_state")
    assert connector_attempt["status"] == "completed"
    assert connector_attempt["operation_revision"] > 0
    assert connector_attempt["plan_digest"].startswith("sha256:")
    assert connector_attempt["execution_spec_digest"].startswith("sha256:")
    assert connector_attempt["target"] == completed.target
    assert connector_attempt["lease_epoch"] > 0
    assert connector_attempt["process_instance_id"]


def test_recovery_marks_started_attempt_indeterminate_and_blocks_effect_retry(
    tmp_path: Path,
) -> None:
    controller = OperationController(FileStore(tmp_path / ".rexecop"))
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    plan = controller.store.load_plan(operation.id)
    journal = AttemptJournal(controller.store.root)
    attempt = journal.start(
        operation_id=operation.id,
        operation_revision=operation.operation_revision,
        step_id="effect-step",
        plan=plan.as_dict(),
        execution_spec={"digest": "sha256:" + "a" * 64},
        target=operation.target,
        mode="apply",
        lease={"lease_epoch": 7, "process_instance_id": "crashed-process"},
    )

    report = run_startup_recovery(controller.store, controller=controller)

    record_path = (
        controller.store.root / "attempts" / operation.id / f"{attempt['attempt_id']}.json"
    )
    recovered = json.loads(record_path.read_text(encoding="utf-8"))
    assert recovered["status"] == "indeterminate"
    assert recovered["error_class"] == "outcome_indeterminate"
    assert report["summary"]["indeterminate_attempt_count"] == 1
    with pytest.raises(RExecOpValidationError, match="outcome_indeterminate"):
        controller.retry(operation.id)
