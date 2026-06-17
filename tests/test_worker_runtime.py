from __future__ import annotations

import json
from pathlib import Path

from rexecop.evidence.event import EvidenceEventType
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.runtime_ops.worker import drain_queue, run_worker, trigger_operation
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"


def test_queue_drain_starts_approved_operation(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )
    completed = controller.start(operation.id)
    assert completed.state == OperationState.COMPLETED.value

    started = drain_queue(controller)
    assert started == []


def test_worker_run_once_is_noop_without_queue(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    started = run_worker(controller, once=True)
    assert started == []


def test_trigger_emits_operation_triggered_event(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = trigger_operation(
        controller,
        profile=str(PROFILE),
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
        source="test",
    )
    events = store.list_evidence_events(operation.id)
    types = [event.get("event_type") for event in events]
    assert EvidenceEventType.OPERATION_TRIGGERED.value in types


def test_trigger_auto_start_completes_readonly(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = trigger_operation(
        controller,
        profile=str(PROFILE),
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
        source="test",
        auto_start=True,
    )
    assert operation.state == OperationState.COMPLETED.value


def test_worker_processes_inbox_trigger(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    inbox = store.root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    payload = {
        "profile": str(PROFILE),
        "env": str(ENVIRONMENT),
        "intent": "check_backup_status",
        "target": "all_critical_vms",
        "mode": "dry_run",
        "auto_start": True,
    }
    (inbox / "job-1.json").write_text(json.dumps(payload))
    started = run_worker(controller, once=True, watch_inbox=True)
    assert started
    assert not list(inbox.glob("job-1.json"))
