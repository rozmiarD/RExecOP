from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.operation.model import Operation
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]
FAILED_PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"


def _json_error(result) -> dict:
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["schema"] == "rexecop.cli_error.v0.1"
    assert payload["status"] == "error"
    assert payload["message"]
    assert isinstance(payload["safe_next_actions"], list)
    assert "raw secrets" in " ".join(payload["non_claims"])
    return payload


def test_operation_explain_missing_operation_uses_cli_error_schema(tmp_path: Path) -> None:
    root = tmp_path / "runtime"

    result = runner.invoke(
        app,
        ["--root", str(root), "operation", "explain", "--operation", "op-missing"],
    )

    payload = _json_error(result)
    assert payload["error_class"] == "validation_error"
    assert payload["reason_code"] == "operation_lookup_failed"
    assert payload["command"] == "operation explain"
    assert "op-missing" in payload["message"]


def test_ops_blockers_use_cli_error_schema_with_details(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    store = FileStore(root)
    store.ensure_layout()
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    store.save_operation(
        Operation(
            id="op-failed-cli-error",
            profile="runtime-fixture",
            environment="runtime-fixture",
            intent="inspect_fixture_state",
            target="fixture-target",
            mode="dry_run",
            requested_by="operator",
            state=OperationState.FAILED.value,
            created_at=now,
            updated_at=now,
        )
    )

    result = runner.invoke(app, ["--root", str(root), "ops"])

    payload = _json_error(result)
    assert payload["error_class"] == "runtime_failure"
    assert payload["reason_code"] == "runtime_blockers_present"
    assert payload["command"] == "ops"
    assert payload["details"]["schema"] == "rexecop.ops.v0.1"
    assert any(
        item["operation_id"] == "op-failed-cli-error"
        for item in payload["details"]["action_required"]
    )


def test_profile_lint_failed_uses_cli_error_schema() -> None:
    result = runner.invoke(
        app,
        ["profile", "lint", "--profile", str(FAILED_PROFILE), "--track", "readonly"],
    )

    payload = _json_error(result)
    assert payload["error_class"] == "validation_error"
    assert payload["reason_code"] == "profile_conformance_failed"
    assert payload["command"] == "profile lint"
    assert payload["details"]["schema"] == "rexecop.profile_conformance.v0.1"
    assert payload["details"]["status"] == "failed"


def test_support_bundle_unredacted_uses_cli_error_schema(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--root", str(tmp_path / "runtime"), "support", "bundle", "op-1"])

    payload = _json_error(result)
    assert payload["reason_code"] == "support_bundle_unavailable"
    assert payload["command"] == "support bundle"
