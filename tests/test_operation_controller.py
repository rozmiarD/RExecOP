from __future__ import annotations

from pathlib import Path

import pytest

from rexecop.errors import RExecOpStateError
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState, validate_transition
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"


def test_plan_creates_operation_and_plan(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )

    assert operation.state == OperationState.PLANNED.value
    assert (store.plans_dir / f"{operation.id}.json").is_file()
    assert (store.operations_dir / f"{operation.id}.json").is_file()


def test_plan_emits_operation_created_and_plan_generated(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
    )

    events = store.list_evidence_events(operation.id)
    event_types = [event["event_type"] for event in events]
    assert "operation_created" in event_types
    assert "plan_generated" in event_types
    assert "state_transition" in event_types


def test_plan_does_not_execute_connectors(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
    )

    events = store.list_evidence_events(operation.id)
    assert not any(event["event_type"] == "step_started" for event in events)
    plan = store.load_plan(operation.id)
    assert plan.planned_steps
    assert operation.current_step_id == ""


def test_dry_run_plan_has_no_govengine_decision(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = OperationController(store=store).plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )
    assert operation.govengine_decision_type == ""


def test_invalid_manual_transition_helper() -> None:
    with pytest.raises(RExecOpStateError):
        validate_transition(OperationState.CREATED, OperationState.RUNNING)
