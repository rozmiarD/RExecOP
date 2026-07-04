from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.operation.review import render_operation_review, review_operation
from rexecop.profile.runbook import render_runbook_show, show_profile_runbook

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "examples" / "profiles" / "runtime-fixture" / "profile.yaml"
POLICY_ENVIRONMENT = ROOT / "examples" / "environments" / "runtime-fixture.policy.example.yaml"
FIRST_RUN_PROFILE = ROOT / "examples" / "first-run-demo" / "profile" / "profile.yaml"

runner = CliRunner()


def test_review_operation_reports_decision_screen_fields() -> None:
    operation = Operation(
        id="op-review-1",
        profile="runtime-fixture",
        environment="runtime-fixture-policy",
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
        requested_by="operator",
        state="planned",
        created_at="2026-07-04T00:00:00+00:00",
        updated_at="2026-07-04T00:00:00+00:00",
        metadata={
            "profile_root": str(PROFILE.parent),
            "environment_path": str(POLICY_ENVIRONMENT),
            "environment_connectors": {
                "fixture_source": {"backend": "static_fixture"},
            },
            "policy_verdict": {
                "decision": "allow",
                "reason_code": "fixture_inspect_allowed",
                "blockers": [],
            },
            "policy_enforcement": {
                "plan": {
                    "plan_id": "plan-1",
                    "status": "ready",
                    "reason_code": "policy_controls_projected",
                    "blockers": [],
                },
                "plan_digest": "sha256:" + "1" * 64,
                "admission": {
                    "decision_id": "admission-1",
                    "outcome": "allowed",
                },
                "admission_digest": "sha256:" + "2" * 64,
            },
        },
    )
    plan = OperationPlan(
        operation_id="op-review-1",
        profile="runtime-fixture",
        environment="runtime-fixture-policy",
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
        workflow={"id": "runtime_fixture.inspect_fixture_state"},
        planned_steps=[
            {
                "id": "inspect_state",
                "type": "connector",
                "connector": "fixture_source",
                "action": "read_fixture_state",
                "pause_safe": False,
            },
            {
                "id": "produce_receipt",
                "type": "evidence",
                "action": "produce_receipt",
                "pause_safe": True,
            },
        ],
        required_connectors=["fixture_source"],
        risk="low",
        govengine_request_preview={"policy_decision": {"decision": "allow"}},
        expected_evidence=["plan_generated", "state_transition"],
        pause_safe_points=["produce_receipt"],
        retry_policy_summary={"max_attempts": 0},
        rollback_available=False,
    )

    payload = review_operation(operation, plan)

    assert payload["schema"] == "rexecop.operation_review.v0.1"
    assert payload["status"] == "proceed"
    screen = payload["decision_screen"]
    assert screen["operation_id"] == "op-review-1"
    assert screen["side_effect_class"] == "none"
    assert screen["runbook_ref"] == "workflows/inspect_fixture_state.yaml"
    assert screen["backends"] == [
        {"connector": "fixture_source", "backend": "static_fixture"}
    ]
    assert "pause_safe:produce_receipt" in screen["stop_conditions"]
    assert screen["expected_evidence"] == ["plan_generated", "state_transition"]
    assert screen["safe_next_actions"] == ["rexecop start --operation op-review-1"]


def test_review_operation_marks_blocked_policy() -> None:
    operation = Operation(
        id="op-review-2",
        profile="runtime-fixture",
        environment="runtime-fixture-policy",
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
        requested_by="operator",
        state="planned",
        created_at="2026-07-04T00:00:00+00:00",
        updated_at="2026-07-04T00:00:00+00:00",
        metadata={
            "profile_root": str(PROFILE.parent),
            "policy_verdict": {
                "decision": "deny",
                "reason_code": "fixture_mutation_denied",
                "blockers": ["fixture_mutation_denied"],
            },
            "policy_enforcement": {
                "plan": {
                    "plan_id": "plan-2",
                    "status": "blocked",
                    "reason_code": "fixture_mutation_denied",
                    "blockers": ["fixture_mutation_denied"],
                },
            },
        },
    )
    plan = OperationPlan(
        operation_id="op-review-2",
        profile="runtime-fixture",
        environment="runtime-fixture-policy",
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
        workflow={"id": "runtime_fixture.apply_fixture_change"},
        planned_steps=[],
        required_connectors=[],
        risk="high",
        govengine_request_preview={},
        expected_evidence=[],
        pause_safe_points=[],
        retry_policy_summary={},
        rollback_available=False,
    )

    payload = review_operation(operation, plan)

    assert payload["status"] == "blocked"
    assert "fixture_mutation_denied" in payload["decision_screen"]["governance_blockers"]


def test_render_operation_review_formats_are_stable() -> None:
    payload = {
        "schema": "rexecop.operation_review.v0.1",
        "status": "proceed",
        "decision_screen": {
            "operation_id": "op-1",
            "intent": "inspect",
            "target": "fixture-target",
            "mode": "dry_run",
            "state": "planned",
            "side_effect_class": "none",
            "digests": {
                "profile_digest": "abc",
                "environment_digest": "def",
                "catalog_digest": "ghi",
            },
            "runbook_ref": "docs/inspect.md",
            "backends": [{"connector": "fixture", "backend": "static_fixture"}],
            "governance_blockers": [],
            "safe_next_actions": ["rexecop start --operation op-1"],
        },
    }

    table = render_operation_review(payload, "table")
    markdown = render_operation_review(payload, "markdown")

    assert "operation_id" in table and "op-1" in table
    assert "fixture:static_fixture" in table
    assert "# Operation review" in markdown
    assert "docs/inspect.md" in markdown


def test_review_operation_includes_catalog_metadata_when_available() -> None:
    operation = Operation(
        id="op-review-demo",
        profile="first-run-demo",
        environment="first-run-demo",
        intent="inspect",
        target="fixture-target",
        mode="dry_run",
        requested_by="operator",
        state="planned",
        created_at="2026-07-04T00:00:00+00:00",
        updated_at="2026-07-04T00:00:00+00:00",
        metadata={
            "profile_root": str(FIRST_RUN_PROFILE.parent),
        },
    )
    plan = OperationPlan(
        operation_id="op-review-demo",
        profile="first-run-demo",
        environment="first-run-demo",
        intent="inspect",
        target="fixture-target",
        mode="dry_run",
        workflow={"id": "first_run_demo.inspect"},
        planned_steps=[],
        required_connectors=["fixture"],
        risk="low",
        govengine_request_preview={},
        expected_evidence=["plan_generated"],
        pause_safe_points=[],
        retry_policy_summary={},
        rollback_available=False,
    )

    payload = review_operation(operation, plan)

    assert payload["decision_screen"]["side_effect_class"] == "none"
    assert payload["decision_screen"]["runbook_ref"] == "docs/inspect.md"


def test_show_profile_runbook_is_profile_bound() -> None:
    payload = show_profile_runbook(FIRST_RUN_PROFILE, "inspect")

    assert payload["schema"] == "rexecop.runbook_show.v0.1"
    assert payload["runbook_ref"] == "docs/inspect.md"
    assert payload["profile_digest"]
    assert payload["runbook_digest"]
    assert "First-Run Demo Inspect" in payload["content"]


def test_cli_operation_review_and_runbook_show(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    plan_result = runner.invoke(
        app,
        [
            "--root",
            str(root),
            "plan",
            "--profile",
            str(PROFILE),
            "--env",
            str(POLICY_ENVIRONMENT),
            "--intent",
            "inspect_fixture_state",
            "--target",
            "fixture-target",
            "--mode",
            "dry_run",
        ],
    )
    assert plan_result.exit_code == 0, plan_result.output
    operation_id = plan_result.stdout.strip()

    review_json = runner.invoke(
        app,
        [
            "--root",
            str(root),
            "operation",
            "review",
            "--operation",
            operation_id,
        ],
    )
    assert review_json.exit_code == 0, review_json.output
    payload = yaml.safe_load(review_json.stdout)
    assert payload["schema"] == "rexecop.operation_review.v0.1"
    assert payload["status"] == "proceed"
    assert payload["decision_screen"]["backends"]

    review_table = runner.invoke(
        app,
        [
            "--root",
            str(root),
            "operation",
            "review",
            "--operation",
            operation_id,
            "--format",
            "table",
        ],
    )
    assert review_table.exit_code == 0, review_table.output
    assert operation_id in review_table.stdout

    runbook = runner.invoke(
        app,
        [
            "runbook",
            "show",
            "inspect",
            "--profile",
            str(FIRST_RUN_PROFILE),
            "--format",
            "markdown",
        ],
    )
    assert runbook.exit_code == 0, runbook.output
    assert "First-Run Demo Inspect" in runbook.stdout

    runbook_json = runner.invoke(
        app,
        [
            "runbook",
            "show",
            "inspect",
            "--profile",
            str(FIRST_RUN_PROFILE),
        ],
    )
    assert runbook_json.exit_code == 0, runbook_json.output
    runbook_payload = yaml.safe_load(runbook_json.stdout)
    assert runbook_payload["schema"] == "rexecop.runbook_show.v0.1"
    assert render_runbook_show(runbook_payload, "table")