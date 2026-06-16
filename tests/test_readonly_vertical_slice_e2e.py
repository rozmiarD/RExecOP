from __future__ import annotations

from pathlib import Path

from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"


def test_readonly_vertical_slice_e2e(tmp_path: Path) -> None:
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

    completed = controller.start(operation.id)
    assert completed.state == OperationState.COMPLETED.value

    events = store.list_evidence_events(operation.id)
    event_types = [event["event_type"] for event in events]
    assert "step_started" in event_types
    assert "step_completed" in event_types
    assert "validation_completed" in event_types
    assert "operation_completed" in event_types
    assert "receipt_generated" in event_types

    validation = controller.validate(operation.id)
    assert validation["passed"] is True
    assert completed.sclite_refs.get("intent_contract")
