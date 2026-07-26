from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.cli_contracts import CLI_CONTRACTS
from rexecop.operation.model import Operation
from rexecop.operation.state import OperationState
from rexecop.runtime.init import initialize_runtime_root
from rexecop.storage.file_store import FileStore

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"
FAILED_PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"

REGISTRY_COMMANDS = frozenset(" ".join(item.command) for item in CLI_CONTRACTS)


def _json_error(result) -> dict:
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["schema"] == "rexecop.cli_error.v0.1"
    assert payload["status"] == "error"
    assert payload["message"]
    assert isinstance(payload["safe_next_actions"], list)
    assert "raw secrets" in " ".join(payload["non_claims"])
    return payload


def _json_success(result) -> dict:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _invoke(root: Path, *args: str):
    return runner.invoke(app, ["--root", str(root), *args])


def _planned_operation(tmp_path: Path):
    root = tmp_path / ".rexecop"
    initialize_runtime_root(root)
    store = FileStore(root)
    store.ensure_layout()
    from rexecop.operation.controller import OperationController

    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    return root, operation


def test_start_reports_stable_mutation_posture_reason(tmp_path: Path) -> None:
    from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
    from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
    from rexecop.operation.controller import OperationController

    root = tmp_path / ".rexecop"
    initialize_runtime_root(root)
    controller = OperationController(
        store=FileStore(root),
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="apply",
    )

    payload = _json_error(
        _invoke(root, "--json", "start", "--operation", operation.id)
    )

    assert payload["command"] == "start"
    assert payload["reason_code"] == "mutation_not_certified"
    assert "stable execution" in " ".join(payload["safe_next_actions"])


@pytest.mark.parametrize(
    ("args", "expected_command", "expected_reason"),
    [
        (
            ("operation", "explain", "--operation", "op-missing"),
            "operation explain",
            "operation_lookup_failed",
        ),
        (
            ("status", "--operation", "op-missing"),
            "status",
            "operation_lookup_failed",
        ),
        (
            ("operation", "review", "--operation", "op-missing"),
            "operation review",
            "operation_lookup_failed",
        ),
        (
            ("operation", "diff", "--operation", "op-missing"),
            "operation diff",
            "operation_lookup_failed",
        ),
        (
            ("receipt", "show", "op-missing"),
            "receipt show",
            "receipt_lookup_failed",
        ),
        (
            ("evidence", "show", "op-missing"),
            "evidence show",
            "operation_lookup_failed",
        ),
        (
            ("chain", "summary", "op-missing"),
            "chain summary",
            "operation_lookup_failed",
        ),
        (
            ("chain", "explain", "op-missing"),
            "chain explain",
            "operation_lookup_failed",
        ),
        (
            ("reaction", "explain", "--reaction", "reaction-missing"),
            "reaction explain",
            "reaction_lookup_failed",
        ),
        (
            (
                "reaction-proposal-review",
                "--profile",
                str(PROFILE),
                "--proposal",
                "missing.json",
            ),
            "reaction-proposal-review",
            "proposal_review_failed",
        ),
        (
            (
                "reaction-proposal-submit",
                "--profile",
                str(PROFILE),
                "--proposal",
                "missing.json",
                "--decision",
                "accept_for_planning",
                "--reviewer",
                "operator:test",
                "--reason",
                "test",
            ),
            "reaction-proposal-submit",
            "proposal_submit_failed",
        ),
        (
            ("support", "bundle", "op-missing"),
            "support bundle",
            "support_bundle_unavailable",
        ),
        (
            ("dead-letter", "show", "missing.json"),
            "dead-letter show",
            "dead_letter_lookup_failed",
        ),
        (
            ("explain-error", "unsupported-ref"),
            "explain-error",
            "explain_error_unavailable",
        ),
        (
            ("runtime", "status", "--no-json"),
            "runtime status",
            "unsupported_output_format",
        ),
        (
            ("runtime", "reconstruct-status", "--no-json"),
            "runtime reconstruct-status",
            "unsupported_output_format",
        ),
    ],
)
def test_registry_commands_emit_cli_error_on_failure(
    tmp_path: Path,
    args: tuple[str, ...],
    expected_command: str,
    expected_reason: str,
) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    result = _invoke(root, *args)
    payload = _json_error(result)
    assert payload["command"] == expected_command
    assert payload["reason_code"] == expected_reason


def test_ops_blockers_use_cli_error_schema_with_details(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
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

    payload = _json_error(_invoke(root, "ops"))
    assert payload["error_class"] == "runtime_failure"
    assert payload["reason_code"] == "runtime_blockers_present"
    assert payload["command"] == "ops"
    assert payload["details"]["schema"] == "rexecop.ops.v0.1"
    assert any(
        item["operation_id"] == "op-failed-cli-error"
        for item in payload["details"]["action_required"]
    )


def test_profile_lint_failed_uses_cli_error_schema() -> None:
    payload = _json_error(
        runner.invoke(
            app,
            [
                "--json",
                "profile",
                "lint",
                "--profile",
                str(FAILED_PROFILE),
                "--track",
                "readonly",
            ],
        )
    )
    assert payload["error_class"] == "validation_error"
    assert payload["reason_code"] == "profile_conformance_failed"
    assert payload["command"] == "profile lint"
    assert payload["details"]["schema"] == "rexecop.profile_conformance.v0.1"
    assert payload["details"]["status"] == "failed"


def test_support_bundle_unredacted_uses_cli_error_schema(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    payload = _json_error(_invoke(root, "support", "bundle", "op-1"))
    assert payload["reason_code"] == "support_bundle_unavailable"
    assert payload["command"] == "support bundle"


def test_audit_group_success_schemas(tmp_path: Path) -> None:
    root, operation = _planned_operation(tmp_path)

    evidence = _json_success(_invoke(root, "evidence", "show", operation.id))
    assert evidence["schema"] == "rexecop.evidence_show.v0.1"

    chain = _json_success(_invoke(root, "chain", "summary", operation.id))
    assert chain["schema"] == "rexecop.chain_summary.v0.1"


def test_operation_group_success_schemas(tmp_path: Path) -> None:
    root, operation = _planned_operation(tmp_path)

    status = _json_success(_invoke(root, "status", "--operation", operation.id))
    assert status["schema"] == "rexecop.operation_status.v0.1"

    review = _json_success(
        _invoke(root, "operation", "review", "--operation", operation.id)
    )
    assert review["schema"] == "rexecop.operation_review.v0.1"


def test_runtime_group_success_schemas(tmp_path: Path) -> None:
    root, _operation = _planned_operation(tmp_path)

    dead_letters = _json_success(_invoke(root, "dead-letter", "list"))
    assert dead_letters["schema"] == "rexecop.dead_letter_list.v0.1"

    locks = _json_success(_invoke(root, "locks", "list"))
    assert locks["schema"] == "rexecop.locks_list.v0.1"

    runtime_status = _json_success(_invoke(root, "runtime", "status", "--json"))
    assert runtime_status["schema"] == "rexecop.runtime_status.v0.1"

    reconstruction = _json_success(
        _invoke(root, "runtime", "reconstruct-status", "--json")
    )
    assert reconstruction["schema"] == "rexecop.runtime_reconstruction.v0.1"


def test_governance_controls_profile_not_found_uses_cli_error_schema() -> None:
    payload = _json_error(
        runner.invoke(
            app,
            [
                "--json",
                "governance",
                "controls",
                "--profile",
                "/nonexistent/profile.yaml",
            ],
        )
    )
    assert payload["error_class"] == "validation_error"
    assert payload["reason_code"] == "validation_error"
    assert payload["command"] == "governance controls"


def test_all_registry_commands_have_cli_error_failure_coverage() -> None:
    covered = {
        "status",
        "operation explain",
        "operation review",
        "operation diff",
        "receipt show",
        "evidence show",
        "chain explain",
        "chain summary",
        "reaction explain",
        "support bundle",
        "runtime status",
        "dead-letter list",
        "dead-letter show",
        "locks list",
        "explain-error",
        "ops",
        "profile lint",
        "governance controls",
        "reaction-proposal-review",
        "reaction-proposal-submit",
        "runtime reconstruct-status",
        "observability logs list",
        "observability diagnostics",
    }
    assert REGISTRY_COMMANDS == covered
