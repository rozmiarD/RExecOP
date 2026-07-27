from __future__ import annotations

import json
import os
import stat
import uuid
from pathlib import Path

import pytest

from rexecop.errors import RExecOpValidationError
from rexecop.evidence.event import EvidenceEventType
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.runtime_ops import inbox as inbox_runtime
from rexecop.runtime_ops import worker as worker_runtime
from rexecop.runtime_ops.worker import drain_queue, run_worker, trigger_operation
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def test_queue_drain_starts_approved_operation(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
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
        intent="inspect_fixture_state",
        target="fixture-target",
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
        intent="inspect_fixture_state",
        target="fixture-target",
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
        "intent": "inspect_fixture_state",
        "target": "fixture-target",
        "mode": "dry_run",
        "auto_start": True,
    }
    (inbox / "job-1.json").write_text(json.dumps(payload))
    started = run_worker(controller, once=True, watch_inbox=True)
    assert started
    assert not list(inbox.glob("job-1.json"))
    assert inbox.stat().st_mode & 0o777 == 0o700


def test_worker_quarantines_malformed_once_then_executes_valid_across_polls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    inbox = store.root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "00-bad-job.json").write_text(
        json.dumps({"profile": str(PROFILE), "private_note": "never-log-this-token"})
    )
    (inbox / "01-valid-job.json").write_text(
        json.dumps(
            {
                "profile": str(PROFILE),
                "env": str(ENVIRONMENT),
                "intent": "inspect_fixture_state",
                "target": "fixture-target",
                "mode": "dry_run",
                "auto_start": True,
            }
        )
    )
    monkeypatch.setattr(worker_runtime.time, "sleep", lambda _: None)

    started = run_worker(
        controller,
        watch_inbox=True,
        poll_interval=0.01,
        max_iterations=3,
    )

    assert len(started) == 1
    assert not list(inbox.glob("*.json"))
    quarantined = list((inbox / "failed").glob("inbox-*.json"))
    assert len(quarantined) == 1
    logs = [
        event
        for event in store.list_structured_log_events(limit=200)
        if event["event_kind"] == "inbox_item_quarantined"
    ]
    assert len(logs) == 1
    assert logs[0]["correlation_id"] == "worker-inbox"
    log_text = json.dumps(logs[0], sort_keys=True)
    assert "00-bad-job.json" not in log_text
    assert "never-log-this-token" not in log_text


def test_worker_quarantine_uses_second_fixed_target_after_uuid_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    inbox = store.root / "inbox"
    inbox.mkdir(parents=True)
    source = inbox / ("x" * 250 + ".json")
    source.write_text(json.dumps({"profile": str(PROFILE)}), encoding="utf-8")
    failed = inbox / "failed"
    failed.mkdir(mode=0o700)
    first = uuid.UUID(hex="1" * 32)
    second = uuid.UUID(hex="2" * 32)
    collision = failed / f"inbox-{first.hex}.json"
    collision.write_text("existing-target", encoding="utf-8")
    generated = iter((first, second))
    monkeypatch.setattr(inbox_runtime.uuid, "uuid4", lambda: next(generated, second))

    assert run_worker(controller, once=True, watch_inbox=True) == []

    assert collision.read_text(encoding="utf-8") == "existing-target"
    second_target = failed / f"inbox-{second.hex}.json"
    assert second_target.is_file()
    assert len(second_target.name) == len("inbox-") + 32 + len(".json")
    assert source.name not in second_target.name


def test_worker_replace_permission_error_fail_stops_then_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    inbox = store.root / "inbox"
    inbox.mkdir(parents=True)
    malformed = inbox / "00-bad-job.json"
    valid = inbox / "01-valid-job.json"
    malformed.write_text(json.dumps({"profile": str(PROFILE)}), encoding="utf-8")
    valid.write_text(
        json.dumps(
            {
                "profile": str(PROFILE),
                "env": str(ENVIRONMENT),
                "intent": "inspect_fixture_state",
                "target": "fixture-target",
                "mode": "dry_run",
                "auto_start": True,
            }
        ),
        encoding="utf-8",
    )
    real_replace = os.replace
    permission_error_executed = False

    def deny_quarantine(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        **kwargs: object,
    ) -> None:
        nonlocal permission_error_executed
        if source == malformed.name:
            assert isinstance(kwargs.get("src_dir_fd"), int)
            assert isinstance(kwargs.get("dst_dir_fd"), int)
            permission_error_executed = True
            raise PermissionError(13, "raw-denial", str(malformed))
        real_replace(source, destination, **kwargs)

    monkeypatch.setattr(inbox_runtime.os, "replace", deny_quarantine)
    with pytest.raises(RExecOpValidationError) as raised:
        run_worker(controller, once=True, watch_inbox=True)

    assert str(raised.value) == "inbox item quarantine failed"
    assert permission_error_executed is True
    assert str(tmp_path) not in str(raised.value)
    assert "raw-denial" not in str(raised.value)
    assert malformed.is_file()
    assert valid.is_file()
    assert not list((inbox / "failed").glob("inbox-*.json"))
    failure_logs = [
        event
        for event in store.list_structured_log_events(limit=200)
        if event["event_kind"] == "inbox_quarantine_failed"
    ]
    assert len(failure_logs) == 1
    assert str(tmp_path) not in json.dumps(failure_logs[0], sort_keys=True)

    monkeypatch.setattr(inbox_runtime.os, "replace", real_replace)
    started = run_worker(controller, once=True, watch_inbox=True)
    assert len(started) == 1
    assert not list(inbox.glob("*.json"))
    assert len(list((inbox / "failed").glob("inbox-*.json"))) == 1


@pytest.mark.parametrize("failing_call", ["fstat", "fchmod"])
def test_quarantine_cleans_owned_reservation_after_descriptor_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing_call: str,
) -> None:
    inbox = tmp_path / "runtime" / "inbox"
    inbox.mkdir(parents=True, mode=0o700)
    inbox.chmod(0o700)
    source = inbox / "bad-job.json"
    source.write_text(json.dumps({"profile": "missing"}), encoding="utf-8")
    failed = inbox / "failed"
    inbox_runtime.prepare_inbox_destination(failed)
    real_call = getattr(os, failing_call)

    def fail_reservation(descriptor: int, *args: object) -> object:
        try:
            opened_path = os.readlink(f"/proc/self/fd/{descriptor}")
        except OSError:
            opened_path = ""
        if "/failed/inbox-" in opened_path:
            raise OSError(5, "raw-reservation-failure", str(source))
        return real_call(descriptor, *args)

    monkeypatch.setattr(inbox_runtime.os, failing_call, fail_reservation)

    with pytest.raises(RExecOpValidationError) as raised:
        inbox_runtime.quarantine_inbox_item(source, failed)

    assert str(raised.value) == "inbox item quarantine failed"
    assert "raw-reservation-failure" not in str(raised.value)
    assert str(tmp_path) not in str(raised.value)
    assert source.is_file()
    assert list(failed.iterdir()) == []


def test_quarantine_cleans_owned_reservation_after_initial_fd_stat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = tmp_path / "runtime" / "inbox"
    inbox.mkdir(parents=True, mode=0o700)
    inbox.chmod(0o700)
    source = inbox / "bad-job.json"
    source.write_text(json.dumps({"profile": "missing"}), encoding="utf-8")
    failed = inbox / "failed"
    inbox_runtime.prepare_inbox_destination(failed)
    real_stat = os.stat
    injected = False

    def fail_initial_reservation_stat(
        path: int | os.PathLike[str] | str,
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        nonlocal injected
        if isinstance(path, int):
            try:
                opened_path = os.readlink(f"/proc/self/fd/{path}")
            except OSError:
                opened_path = ""
            if "/failed/inbox-" in opened_path and not injected:
                injected = True
                raise OSError(5, "raw-initial-stat-failure", str(source))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(inbox_runtime.os, "stat", fail_initial_reservation_stat)

    with pytest.raises(RExecOpValidationError) as raised:
        inbox_runtime.quarantine_inbox_item(source, failed)

    assert injected is True
    assert str(raised.value) == "inbox item quarantine failed"
    assert "raw-initial-stat-failure" not in str(raised.value)
    assert str(tmp_path) not in str(raised.value)
    assert source.is_file()
    assert list(failed.iterdir()) == []


def test_quarantine_rejects_source_identity_swap_before_replace(tmp_path: Path) -> None:
    inbox = tmp_path / "runtime" / "inbox"
    inbox.mkdir(parents=True, mode=0o700)
    inbox.chmod(0o700)
    source = inbox / "bad-job.json"
    source.write_text("original-item", encoding="utf-8")
    replacement = tmp_path / "replacement.json"
    replacement.write_text("replacement-item", encoding="utf-8")
    displaced = tmp_path / "displaced-original.json"
    failed = inbox / "failed"
    inbox_runtime.prepare_inbox_destination(failed)

    def swap_source(_: Path) -> None:
        source.replace(displaced)
        replacement.replace(source)

    with pytest.raises(RExecOpValidationError) as raised:
        inbox_runtime.quarantine_inbox_item(
            source,
            failed,
            before_move=swap_source,
        )

    assert str(raised.value) == "inbox item quarantine failed"
    assert source.read_text(encoding="utf-8") == "replacement-item"
    assert displaced.read_text(encoding="utf-8") == "original-item"
    assert list(failed.iterdir()) == []


@pytest.mark.parametrize("watchdog", [False, True])
@pytest.mark.parametrize("source_kind", ["symlink", "fifo"])
def test_worker_rejects_symlink_or_nonregular_inbox_item_without_following(
    tmp_path: Path,
    source_kind: str,
    watchdog: bool,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    inbox = store.root / "inbox"
    inbox.mkdir(parents=True)
    source = inbox / "bad-job.json"
    external = tmp_path / "external.json"
    external.write_text("external-target-intact", encoding="utf-8")
    external.chmod(0o640)
    if source_kind == "symlink":
        source.symlink_to(external)
    else:
        os.mkfifo(source, mode=0o640)

    with pytest.raises(RExecOpValidationError) as raised:
        run_worker(controller, once=True, watch_inbox=True, watchdog=watchdog)

    assert str(raised.value) == "inbox item quarantine failed"
    assert str(tmp_path) not in str(raised.value)
    if source_kind == "symlink":
        assert source.is_symlink()
        assert external.read_text(encoding="utf-8") == "external-target-intact"
        assert external.stat().st_mode & 0o777 == 0o640
    else:
        assert stat.S_ISFIFO(source.lstat().st_mode)
        assert source.lstat().st_mode & 0o777 == 0o640
