from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.operation.controller import OperationController
from rexecop.operation.model import Operation
from rexecop.operation.state import OperationState
from rexecop.runtime_ops.triage import (
    collect_ops_snapshot,
    collect_runtime_status,
    explain_error,
    list_locks,
)
from rexecop.runtime_ops.watchdog import WatchdogService
from rexecop.storage.file_store import FileStore
from test_catalog import _write_fixture

runner = CliRunner()


def _controller(tmp_path: Path) -> OperationController:
    return OperationController(store=FileStore(tmp_path / ".rexecop"))


def _save_operation(store: FileStore, operation: Operation) -> None:
    store.save_operation(operation)


def test_runtime_status_reports_queue_and_dead_letters(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    store = controller.store
    controller.runtime.queue.enqueue("op-queued-1")
    inbox = store.root / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "job.json").write_text("{}", encoding="utf-8")
    WatchdogService(store).move_inbox_item_to_dead_letter(
        inbox / "job.json",
        reason="test_dead_letter",
    )

    payload = collect_runtime_status(store)

    assert payload["schema"] == "rexecop.runtime_status.v0.1"
    assert payload["queue"]["depth"] == 1
    assert payload["dead_letter"]["count"] == 1
    assert payload["inbox"]["count"] == 0


def test_ops_collects_action_required_for_failed_operation(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    store = controller.store
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    operation = Operation(
        id="op-failed-1",
        profile="runtime-fixture",
        environment="runtime-fixture",
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
        requested_by="operator",
        state=OperationState.FAILED.value,
        created_at=now,
        updated_at=now,
        metadata={
            "policy_verdict": {
                "decision": "allow",
                "reason_code": "fixture_inspect_allowed",
                "blockers": [],
            }
        },
    )
    _save_operation(store, operation)

    payload = collect_ops_snapshot(store)

    assert payload["schema"] == "rexecop.ops.v0.1"
    assert payload["blockers"]
    assert payload["blockers"][0]["operation_id"] == "op-failed-1"
    assert payload["blockers"][0]["failure_class"] == "connector"
    assert "rexecop explain-error op-failed-1" in payload["safe_next_actions"]


def test_list_locks_marks_stale_holder(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    store = controller.store
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    completed = Operation(
        id="op-done-1",
        profile="runtime-fixture",
        environment="env-a",
        intent="inspect_fixture_state",
        target="target-a",
        mode="apply",
        requested_by="operator",
        state=OperationState.COMPLETED.value,
        created_at=now,
        updated_at=now,
    )
    _save_operation(store, completed)
    controller.runtime.target_lock.acquire(
        environment="env-a",
        target="target-a",
        operation_id="op-done-1",
    )

    locks = list_locks(store)

    assert len(locks) == 1
    assert locks[0]["stale"] is True


def test_explain_error_for_operation_and_dead_letter(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    store = controller.store
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    operation = Operation(
        id="op-blocked-1",
        profile="runtime-fixture",
        environment="runtime-fixture",
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="apply",
        requested_by="operator",
        state=OperationState.BLOCKED.value,
        created_at=now,
        updated_at=now,
        govengine_decision_type="deny",
        govengine_decision_summary="fixture mutation denied",
        metadata={
            "policy_verdict": {
                "decision": "deny",
                "reason_code": "fixture_mutation_denied",
                "blockers": ["fixture_mutation_denied"],
            }
        },
    )
    _save_operation(store, operation)

    explained = explain_error(store, "op-blocked-1")

    assert explained["schema"] == "rexecop.explain_error.v0.1"
    assert explained["failure_class"] == "policy"
    assert explained["reason_code"] == "fixture_mutation_denied"

    inbox = store.root / "inbox"
    inbox.mkdir(parents=True)
    path = inbox / "secret-job.json"
    path.write_text(
        json.dumps({"private_note": "never-show-this-token", "intent": "inspect"}),
        encoding="utf-8",
    )
    record = WatchdogService(store).move_inbox_item_to_dead_letter(
        path,
        reason="retry_budget_exhausted",
    )
    dead_letter_name = record["payload"]["dead_letter_name"]
    dead_letter_explained = explain_error(store, dead_letter_name)

    assert dead_letter_explained["failure_class"] == "runtime"
    assert "never-show-this-token" not in json.dumps(dead_letter_explained)


def test_explain_error_watchdog_record_includes_govengine_supervisor_explanation(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    store = controller.store
    now = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)
    operation = Operation(
        id="op-stale-1",
        profile="runtime-fixture",
        environment="runtime-fixture",
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
        requested_by="operator",
        state=OperationState.RUNNING.value,
        created_at=(now - timedelta(hours=2)).isoformat(),
        updated_at=(now - timedelta(hours=2)).isoformat(),
        correlation_id="corr-stale-1",
    )
    _save_operation(store, operation)
    record = WatchdogService(store).record_stale_active_operations(
        max_age_seconds=60,
        now=now,
    )
    assert record
    record_id = record[0]["record_id"]
    explained = explain_error(store, record_id)

    assert explained["ref_kind"] == "watchdog_record"
    assert explained["govengine_supervisor_explanation"]["schema_version"] == "v0.1"
    assert explained["watchdog"]["recovery_class"] == "block_autostart"
    assert explained["reason_code"] == "supervisor_action_allowed"
    assert "rexecop ops" in explained["safe_next_actions"]


def test_cli_runtime_ops_and_explain_error(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    init = runner.invoke(app, ["--root", str(root), "init"])
    assert init.exit_code == 0, init.output

    status = runner.invoke(app, ["--root", str(root), "runtime", "status", "--json"])
    assert status.exit_code == 0, status.output
    assert '"schema": "rexecop.runtime_status.v0.1"' in status.output

    ops = runner.invoke(app, ["--root", str(root), "ops"])
    assert ops.exit_code == 0, ops.output
    assert '"schema": "rexecop.ops.v0.1"' in ops.output

    locks = runner.invoke(app, ["--root", str(root), "locks", "list"])
    assert locks.exit_code == 0, locks.output

    dead_letters = runner.invoke(app, ["--root", str(root), "dead-letter", "list"])
    assert dead_letters.exit_code == 0, dead_letters.output


def test_cli_ops_exits_nonzero_when_blockers_present(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    store = controller.store
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    _save_operation(
        store,
        Operation(
            id="op-failed-cli",
            profile="runtime-fixture",
            environment="runtime-fixture",
            intent="inspect_fixture_state",
            target="fixture-target",
            mode="dry_run",
            requested_by="operator",
            state=OperationState.FAILED.value,
            created_at=now,
            updated_at=now,
        ),
    )

    result = runner.invoke(
        app,
        ["--root", str(controller.store.root), "ops"],
    )

    assert result.exit_code == 1
    assert "op-failed-cli" in result.output


def test_cli_catalog_plan_diff_and_ops_flow(tmp_path: Path) -> None:
    _, environment, catalog = _write_fixture(tmp_path)
    root = tmp_path / "runtime"
    plan_result = runner.invoke(
        app,
        [
            "--root",
            str(root),
            "plan",
            "--catalog",
            str(catalog),
            "--intent",
            "observe_status",
            "--target",
            "node-01",
            "--mode",
            "dry_run",
        ],
    )
    assert plan_result.exit_code == 0, plan_result.output
    operation_id = plan_result.stdout.strip()

    status = runner.invoke(app, ["--root", str(root), "runtime", "status", "--json"])
    assert status.exit_code == 0, status.output

    explain = runner.invoke(
        app,
        ["--root", str(root), "explain-error", operation_id],
    )
    assert explain.exit_code == 0, explain.output
    assert '"failure_class"' in explain.output

    data = yaml.safe_load(environment.read_text())
    data["environment"]["description"] = "drifted"
    environment.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    diff = runner.invoke(
        app,
        ["--root", str(root), "operation", "diff", "--operation", operation_id],
    )
    assert diff.exit_code == 1, diff.output
    assert "drifted" in diff.output