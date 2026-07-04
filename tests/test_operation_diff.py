from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.operation.controller import OperationController
from rexecop.operation.diff import diff_operation_plan, render_operation_plan_diff
from test_catalog import _write_fixture

runner = CliRunner()


def test_diff_reports_unchanged_catalog_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, catalog = _write_fixture(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.chdir(runtime)
    controller = OperationController()
    operation = controller.plan(
        profile_path=None,
        environment_path=None,
        catalog_path=catalog,
        intent="observe_status",
        target="node-01",
        mode="dry_run",
    )
    plan = controller.store.load_plan(operation.id)

    payload = diff_operation_plan(operation, plan)

    assert payload["schema"] == "rexecop.operation_plan_diff.v0.1"
    assert payload["status"] == "unchanged"
    assert payload["drift_summary"] == []
    assert payload["catalog_binding"]["captured"] is True
    assert payload["applicability"]["applicable"] is True


def test_diff_detects_environment_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, environment, catalog = _write_fixture(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.chdir(runtime)
    controller = OperationController()
    operation = controller.plan(
        profile_path=None,
        environment_path=None,
        catalog_path=catalog,
        intent="observe_status",
        target="node-01",
        mode="dry_run",
    )
    plan = controller.store.load_plan(operation.id)
    data = yaml.safe_load(environment.read_text())
    data["environment"]["description"] = "drifted after plan"
    environment.write_text(yaml.safe_dump(data, sort_keys=False))

    payload = diff_operation_plan(operation, plan)

    assert payload["status"] == "drifted"
    assert "environment_digest" in payload["drift_summary"]
    assert payload["environment_binding"]["changed"] is True


def test_render_operation_plan_diff_formats() -> None:
    payload = {
        "schema": "rexecop.operation_plan_diff.v0.1",
        "status": "drifted",
        "operation_id": "op-1",
        "drift_summary": ["environment_digest"],
        "catalog_binding": {
            "captured": True,
            "fields": [
                {
                    "field": "environment_digest",
                    "planned": "aaa",
                    "current": "bbb",
                    "changed": True,
                }
            ],
        },
        "safe_next_actions": [
            "Create a new operation plan for the current profile/env/catalog state.",
        ],
    }

    table = render_operation_plan_diff(payload, "table")
    markdown = render_operation_plan_diff(payload, "markdown")

    assert "drifted" in table
    assert "environment_digest" in table
    assert "# Operation plan diff" in markdown


def test_cli_operation_diff_exit_code_on_drift(tmp_path: Path) -> None:
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
    data = yaml.safe_load(environment.read_text())
    data["environment"]["description"] = "drifted after plan"
    environment.write_text(yaml.safe_dump(data, sort_keys=False))

    drifted = runner.invoke(
        app,
        ["--root", str(root), "operation", "diff", "--operation", operation_id],
    )
    assert drifted.exit_code == 1, drifted.output
    assert "drifted" in drifted.output
    assert "environment_digest" in drifted.output

    unchanged = runner.invoke(
        app,
        [
            "--root",
            str(root),
            "operation",
            "diff",
            "--operation",
            operation_id,
            "--format",
            "table",
        ],
    )
    assert unchanged.exit_code == 1, unchanged.output