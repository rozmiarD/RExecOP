from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.runtime_ops.permit import ExecutionPermitManager
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"
NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def _planned(controller: OperationController):
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    return operation, controller.store.load_plan(operation.id)


def test_execution_permit_binds_fresh_runtime_facts(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store)
    operation, plan = _planned(controller)
    lease = store.acquire_execution_lease(worker_id="permit-test")
    spec = {"digest": "sha256:" + "a" * 64}
    target_binding = {"target": operation.target, "destination": {}}
    manager = ExecutionPermitManager(store)
    attempt_id = store.allocate_execution_attempt_id()
    permit = manager.issue(
        operation=operation,
        plan=plan,
        step_id="inspect_state",
        attempt_id=attempt_id,
        execution_spec=spec,
        target_binding=target_binding,
        lease=lease,
        governance_admission_digest="sha256:" + "b" * 64,
        now=NOW,
    )

    manager.require_fresh(
        permit,
        operation=operation,
        plan=plan,
        attempt_id=attempt_id,
        execution_spec=spec,
        target_binding=target_binding,
        lease=lease,
        governance_admission_digest="sha256:" + "b" * 64,
        now=NOW + timedelta(seconds=1),
    )

    assert permit["operation_revision"] == operation.operation_revision
    assert permit["plan_digest"].startswith("sha256:")
    assert permit["lease_epoch"] == lease["lease_epoch"]
    assert permit["authority"]["governance"] == "govengine"
    store.release_execution_lease(lease)


def test_execution_permit_rejects_expiry_and_revision_drift(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store)
    operation, plan = _planned(controller)
    lease = store.acquire_execution_lease(worker_id="permit-test")
    spec = {"digest": "sha256:" + "c" * 64}
    binding = {"target": operation.target, "destination": {}}
    manager = ExecutionPermitManager(store)
    attempt_id = store.allocate_execution_attempt_id()
    permit = manager.issue(
        operation=operation,
        plan=plan,
        step_id="inspect_state",
        attempt_id=attempt_id,
        execution_spec=spec,
        target_binding=binding,
        lease=lease,
        governance_admission_digest="",
        now=NOW,
        ttl_seconds=2,
    )

    with pytest.raises(RExecOpValidationError, match="execution_permit_stale"):
        manager.require_fresh(
            permit,
            operation=operation,
            plan=plan,
            attempt_id=attempt_id,
            execution_spec=spec,
            target_binding=binding,
            lease=lease,
            governance_admission_digest="",
            now=NOW + timedelta(seconds=3),
        )

    operation.metadata["changed"] = True
    store.save_operation(operation)
    with pytest.raises(RExecOpValidationError, match="operation_revision"):
        manager.require_fresh(
            permit,
            operation=operation,
            plan=plan,
            attempt_id=attempt_id,
            execution_spec=spec,
            target_binding=binding,
            lease=lease,
            governance_admission_digest="",
            now=NOW + timedelta(seconds=1),
        )
    store.release_execution_lease(lease)


def test_connector_attempt_references_just_checked_permit(tmp_path: Path) -> None:
    controller = OperationController(FileStore(tmp_path / ".rexecop"))
    operation, _ = _planned(controller)

    controller.start(operation.id)

    permit = controller.store.load_execution_permit(operation.id, "inspect_state")
    attempt_path = next((controller.store.root / "attempts" / operation.id).glob("*.json"))
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    assert attempt["execution_permit_ref"] == permit["permit_digest"]
    assert permit["execution_spec_digest"] == attempt["execution_spec_digest"]
    assert permit["attempt_id"] == attempt["attempt_id"]
    assert permit["governance_binding_mode"] == "legacy_read_only"
    immutable = controller.store.load_execution_permit_for_attempt(
        operation.id,
        attempt["attempt_id"],
    )
    assert immutable["permit_digest"] == attempt["execution_permit_ref"]
