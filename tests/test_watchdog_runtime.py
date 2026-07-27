from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.operation.model import Operation
from rexecop.operation.state import OperationState
from rexecop.runtime_ops import inbox as inbox_runtime
from rexecop.runtime_ops import worker as worker_runtime
from rexecop.runtime_ops.watchdog import WatchdogService
from rexecop.runtime_ops.worker import run_worker
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"
NOW = datetime(2026, 6, 28, 12, 0, 0, tzinfo=UTC)


def _controller(tmp_path: Path) -> OperationController:
    return OperationController(store=FileStore(tmp_path / ".rexecop"))


def _inbox_payload(*, secret: str = "never-write-this-token") -> dict[str, object]:
    return {
        "profile": str(PROFILE),
        "env": str(ENVIRONMENT),
        "intent": "inspect_fixture_state",
        "target": "fixture-target",
        "mode": "dry_run",
        "auto_start": True,
        "private_note": secret,
    }


def _watchdog_records(store: FileStore) -> list[dict[str, object]]:
    records = store.root / "watchdog" / "records"
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(records.glob("*.json"))]


def test_watchdog_records_worker_heartbeat(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    service = WatchdogService(store)

    record = service.record_heartbeat(worker_id="worker-a", now=NOW)

    heartbeat = json.loads((store.root / "watchdog" / "heartbeat.json").read_text())
    assert heartbeat == record
    assert record["schema"] == "rexecop.watchdog_record.v0.1"
    assert record["observation"] == "worker_heartbeat"
    assert record["decision"] == "record_health"
    assert record["payload"]["worker_id"] == "worker-a"
    assert (store.root / "watchdog").stat().st_mode & 0o777 == 0o700
    assert (store.root / "watchdog" / "heartbeat.json").stat().st_mode & 0o777 == 0o600
    artifacts = list((store.root / "watchdog" / "sclite").glob("*.json"))
    assert len(artifacts) == 1
    artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert artifact["artifact_type"] == "watchdog_decision"
    assert artifact["schema_ref"] == "schemas/watchdog_decision.v0.1.schema.json"
    assert artifact["observation"]["record_id"] == record["record_id"]
    assert artifact["admission"]["outcome"] == "record_only"
    admission = service._admit_record(record)["admission"]
    assert admission["metadata"]["governance_flow"] == (
        "planning_admission_adapter.v1"
    )
    assert admission["metadata"]["execution_authority"] is False
    assert "authorization" not in admission
    assert artifact["authority"] == {
        "truth_layer": "sclite",
        "supervisor": "rexecop",
        "policy_authority": "govengine",
        "domain_authority": "runtime-neutral",
        "execution_authority": "rexecop",
    }


def test_watchdog_moves_stale_inbox_to_dead_letter_without_payload_leak(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    inbox = controller.store.root / "inbox"
    inbox.mkdir(parents=True)
    path = inbox / "job-1.json"
    path.write_text(json.dumps(_inbox_payload()), encoding="utf-8")
    stale = (NOW - timedelta(hours=2)).timestamp()
    os.utime(path, (stale, stale))

    records = WatchdogService(controller.store).move_stale_inbox_items(
        max_age_seconds=60,
        now=NOW,
    )

    assert len(records) == 1
    assert not path.exists()
    dead_letters = list((controller.store.root / "dead_letter").glob("*.json"))
    assert len(dead_letters) == 1
    assert dead_letters[0].read_text(encoding="utf-8").find("never-write-this-token") != -1
    record_text = json.dumps(records[0], sort_keys=True)
    assert "never-write-this-token" not in record_text
    assert records[0]["decision"] == "move_to_dead_letter"
    assert records[0]["payload"]["reason"] == "stale_inbox_item"
    artifacts = list((controller.store.root / "watchdog" / "sclite").glob("*.json"))
    artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert artifact["decision"] == "move_to_dead_letter"
    assert artifact["admission"]["allowed"] is True
    assert artifact["affected"]["inbox_item_name"] == "job-1.json"


def test_worker_watchdog_moves_stale_inbox_before_processing(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    inbox = controller.store.root / "inbox"
    inbox.mkdir(parents=True)
    path = inbox / "job-1.json"
    path.write_text(json.dumps(_inbox_payload()), encoding="utf-8")
    stale = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(path, (stale, stale))

    started = run_worker(
        controller,
        once=True,
        watch_inbox=True,
        watchdog=True,
        stale_inbox_seconds=60,
    )

    assert started == []
    assert controller.store.list_operations() == []
    assert not path.exists()
    assert list((controller.store.root / "dead_letter").glob("*.json"))
    records = _watchdog_records(controller.store)
    assert {record["observation"] for record in records} >= {
        "worker_heartbeat",
        "inbox_item",
        "queue_depth",
    }


def test_worker_watchdog_retries_failed_inbox_without_payload_leak(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    inbox = controller.store.root / "inbox"
    inbox.mkdir(parents=True)
    path = inbox / "bad-job.json"
    path.write_text(
        json.dumps({"profile": str(PROFILE), "private_note": "never-write-this-token"}),
        encoding="utf-8",
    )

    started = run_worker(controller, once=True, watch_inbox=True, watchdog=True)

    assert started == []
    assert path.exists()
    assert not list((controller.store.root / "dead_letter").glob("*.json"))
    record_text = json.dumps(_watchdog_records(controller.store), sort_keys=True)
    assert "never-write-this-token" not in record_text
    assert "retry_later" in record_text
    assert "RExecOpValidationError" in record_text
    retry_budget = json.loads(
        (controller.store.root / "watchdog" / "retry_budget.json").read_text(encoding="utf-8")
    )
    assert retry_budget["bad-job.json"]["attempts"] == 1


def test_worker_watchdog_budget_across_five_polls_dead_letters_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    inbox = controller.store.root / "inbox"
    inbox.mkdir(parents=True)
    path = inbox / "00-bad-job.json"
    path.write_text(
        json.dumps({"profile": str(PROFILE), "private_note": "never-write-this-token"}),
        encoding="utf-8",
    )
    valid = inbox / "01-valid-job.json"
    valid.write_text(json.dumps(_inbox_payload()), encoding="utf-8")
    monkeypatch.setattr(worker_runtime.time, "sleep", lambda _: None)

    started = run_worker(
        controller,
        watch_inbox=True,
        watchdog=True,
        inbox_retry_budget=3,
        poll_interval=0.01,
        max_iterations=5,
    )

    assert len(started) == 1
    assert not path.exists()
    assert not valid.exists()
    dead_letters = list((controller.store.root / "dead_letter").glob("*.json"))
    assert len(dead_letters) == 1
    assert dead_letters[0].read_text(encoding="utf-8").find("never-write-this-token") != -1
    records = _watchdog_records(controller.store)
    record_text = json.dumps(records, sort_keys=True)
    assert sum(record["decision"] == "retry_later" for record in records) == 2
    exhausted = [
        record
        for record in records
        if record["payload"].get("reason") == "retry_budget_exhausted"
    ]
    assert len(exhausted) == 1
    assert "retry_budget_exhausted" in record_text
    assert "never-write-this-token" not in record_text
    retry_budget = json.loads(
        (controller.store.root / "watchdog" / "retry_budget.json").read_text(encoding="utf-8")
    )
    assert retry_budget == {}


def test_watchdog_reached_budget_move_failure_retries_after_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    inbox = controller.store.root / "inbox"
    inbox.mkdir(parents=True)
    malformed = inbox / "00-bad-job.json"
    valid = inbox / "01-valid-job.json"
    malformed.write_text(json.dumps({"profile": str(PROFILE)}), encoding="utf-8")
    real_replace = os.replace

    def deny_final_move(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        **kwargs: object,
    ) -> None:
        if source == malformed.name and kwargs.get("src_dir_fd") is not None:
            raise PermissionError(13, "raw-dead-letter-denial", str(malformed))
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(inbox_runtime.os, "replace", deny_final_move)
    monkeypatch.setattr(worker_runtime.time, "sleep", lambda _: None)

    with pytest.raises(RExecOpValidationError) as raised:
        run_worker(
            controller,
            watch_inbox=True,
            watchdog=True,
            inbox_retry_budget=3,
            poll_interval=0.01,
            max_iterations=5,
        )

    assert str(raised.value) == "inbox item quarantine failed"
    assert "raw-dead-letter-denial" not in str(raised.value)
    assert malformed.is_file()
    assert not valid.exists()
    retry_budget = json.loads(
        (controller.store.root / "watchdog" / "retry_budget.json").read_text(encoding="utf-8")
    )
    assert retry_budget[malformed.name]["attempts"] == 3
    records = _watchdog_records(controller.store)
    assert sum(record["decision"] == "retry_later" for record in records) == 2
    assert not any(
        record["payload"].get("reason") == "retry_budget_exhausted" for record in records
    )

    monkeypatch.setattr(inbox_runtime.os, "replace", real_replace)
    valid.write_text(json.dumps(_inbox_payload()), encoding="utf-8")
    started = run_worker(
        controller,
        once=True,
        watch_inbox=True,
        watchdog=True,
        inbox_retry_budget=3,
    )

    assert len(started) == 1
    assert not malformed.exists()
    assert not valid.exists()
    assert len(list((controller.store.root / "dead_letter").glob("inbox-*.json"))) == 1
    repaired_records = _watchdog_records(controller.store)
    exhausted = [
        record
        for record in repaired_records
        if record["payload"].get("reason") == "retry_budget_exhausted"
    ]
    assert len(exhausted) == 1
    assert exhausted[0]["payload"]["details"]["attempts"] == 3
    cleared = json.loads(
        (controller.store.root / "watchdog" / "retry_budget.json").read_text(encoding="utf-8")
    )
    assert cleared == {}


def test_watchdog_persistence_failure_after_move_is_bounded_and_not_cleared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    inbox = controller.store.root / "inbox"
    inbox.mkdir(parents=True)
    malformed = inbox / "00-bad-job.json"
    valid = inbox / "01-valid-job.json"
    malformed.write_text(json.dumps({"profile": str(PROFILE)}), encoding="utf-8")
    valid.write_text(json.dumps(_inbox_payload()), encoding="utf-8")
    real_persist = WatchdogService._persist_record
    persistence_failure_executed = False

    def fail_required_persistence(
        service: WatchdogService,
        record: dict[str, object],
        *,
        admission_context: dict[str, object],
    ) -> None:
        nonlocal persistence_failure_executed
        payload = record.get("payload")
        if isinstance(payload, dict) and payload.get("reason") == "retry_budget_exhausted":
            persistence_failure_executed = True
            raise OSError(5, "raw-persistence-sentinel", str(malformed))
        real_persist(service, record, admission_context=admission_context)

    monkeypatch.setattr(WatchdogService, "_persist_record", fail_required_persistence)

    with pytest.raises(RExecOpValidationError) as raised:
        run_worker(
            controller,
            once=True,
            watch_inbox=True,
            watchdog=True,
            inbox_retry_budget=1,
        )

    assert persistence_failure_executed is True
    assert str(raised.value) == "inbox item quarantine failed"
    assert "raw-persistence-sentinel" not in str(raised.value)
    assert str(tmp_path) not in str(raised.value)
    assert not malformed.exists()
    assert valid.is_file()
    dead_letters = list((controller.store.root / "dead_letter").glob("inbox-*.json"))
    assert len(dead_letters) == 1
    retry_budget = json.loads(
        (controller.store.root / "watchdog" / "retry_budget.json").read_text(encoding="utf-8")
    )
    assert retry_budget[malformed.name]["attempts"] == 1
    records = _watchdog_records(controller.store)
    assert not any(
        record["payload"].get("reason") == "retry_budget_exhausted" for record in records
    )


@pytest.mark.parametrize("topology", ["dead_letter", "inbox"])
def test_watchdog_layout_rejects_symlink_without_chmodding_target(
    tmp_path: Path,
    topology: str,
) -> None:
    controller = _controller(tmp_path)
    external = tmp_path / "external-directory"
    external.mkdir(mode=0o750)
    external.chmod(0o750)
    linked = controller.store.root / topology
    linked.parent.mkdir(parents=True, exist_ok=True)
    linked.symlink_to(external, target_is_directory=True)

    with pytest.raises(RExecOpValidationError) as raised:
        run_worker(
            controller,
            once=True,
            watch_inbox=True,
            watchdog=True,
        )

    assert str(raised.value) == "inbox item quarantine failed"
    assert external.stat().st_mode & 0o777 == 0o750
    assert linked.is_symlink()


def test_watchdog_records_stale_active_operation_blocker(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    old = (NOW - timedelta(hours=3)).isoformat()
    store.save_operation(
        Operation(
            id="op-stale",
            profile="runtime-fixture",
            environment="runtime-fixture",
            intent="inspect_fixture_state",
            target="fixture-target",
            mode="dry_run",
            requested_by="test",
            state=OperationState.RUNNING.value,
            created_at=old,
            updated_at=old,
        )
    )

    records = WatchdogService(store).record_stale_active_operations(
        max_age_seconds=60,
        now=NOW,
    )

    assert len(records) == 1
    assert records[0]["observation"] == "stuck_operation"
    assert records[0]["decision"] == "block_autostart"
    assert records[0]["payload"]["operation_id"] == "op-stale"
    assert records[0]["payload"]["reason"] == "stale_active_operation"
    events = store.list_evidence_events("op-stale")
    assert len(events) == 1
    assert events[0]["event_type"] == "watchdog_decision"
    assert events[0]["sanitized_payload"]["record_id"] == records[0]["record_id"]
    assert events[0]["sanitized_payload"]["decision"] == "block_autostart"
    artifacts = list((store.root / "watchdog" / "sclite").glob("*.json"))
    artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert artifact["decision"] == "block_autostart"
    assert artifact["admission"]["allowed"] is True
    assert artifact["affected"]["operation_id"] == "op-stale"


def test_watchdog_records_manual_recovery_with_admission_and_sclite_artifact(
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")

    record = WatchdogService(store).record_manual_recovery_action(
        action="mark_stale",
        reason="operator_break_glass",
        actor_ref="operator:local-admin",
        scope="operation:op-stale",
        operation_id="op-stale",
        now=NOW,
    )

    assert record["observation"] == "manual_recovery"
    assert record["decision"] == "mark_stale"
    assert record["payload"]["actor_ref"] == "operator:local-admin"
    assert record["payload"]["scope"] == "operation:op-stale"
    artifacts = list((store.root / "watchdog" / "sclite").glob("*.json"))
    assert len(artifacts) == 1
    artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert artifact["decision"] == "mark_stale"
    assert artifact["admission"]["allowed"] is True
    assert artifact["manual_recovery"] == {
        "actor_ref": "operator:local-admin",
        "scope": "operation:op-stale",
        "human_signoff": True,
        "reason": "operator_break_glass",
    }
    assert artifact["affected"]["operation_id"] == "op-stale"


def test_watchdog_manual_recovery_requires_bounded_context(tmp_path: Path) -> None:
    service = WatchdogService(FileStore(tmp_path / ".rexecop"))

    with pytest.raises(RExecOpValidationError, match="manual watchdog actor_ref"):
        service.record_manual_recovery_action(
            action="mark_stale",
            reason="operator_break_glass",
            actor_ref="",
            scope="operation:op-stale",
            operation_id="op-stale",
        )

    with pytest.raises(RExecOpValidationError, match="affected reference"):
        service.record_manual_recovery_action(
            action="mark_stale",
            reason="operator_break_glass",
            actor_ref="operator:local-admin",
            scope="operation:op-stale",
        )


def test_watchdog_rejects_invalid_thresholds(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    service = WatchdogService(controller.store)

    with pytest.raises(RExecOpValidationError, match="max_age_seconds must be positive"):
        service.move_stale_inbox_items(max_age_seconds=0)

    with pytest.raises(RExecOpValidationError, match="stale_inbox_seconds must be positive"):
        run_worker(controller, once=True, watchdog=True, stale_inbox_seconds=0)

    with pytest.raises(RExecOpValidationError, match="stale_operation_seconds must be positive"):
        run_worker(controller, once=True, watchdog=True, stale_operation_seconds=0)

    with pytest.raises(RExecOpValidationError, match="inbox_retry_budget must be positive"):
        run_worker(controller, once=True, watchdog=True, inbox_retry_budget=0)
