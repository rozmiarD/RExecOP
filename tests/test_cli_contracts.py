from __future__ import annotations

import json

from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.cli_contracts import (
    CLI_CONTRACT_REGISTRY_SCHEMA,
    cli_contract_registry,
    validate_cli_contract_registry,
)
from rexecop.cli_errors import CLI_ERROR_SCHEMA

runner = CliRunner()

EXPECTED_COMMAND_SCHEMAS = {
    "chain explain": "rexecop.chain_explain.v0.1",
    "chain summary": "rexecop.chain_summary.v0.1",
    "dead-letter list": "rexecop.dead_letter_list.v0.1",
    "dead-letter show": "rexecop.dead_letter_show.v0.1",
    "evidence show": "rexecop.evidence_show.v0.1",
    "explain-error": "rexecop.explain_error.v0.1",
    "governance controls": "rexecop.governance_controls.v0.1",
    "locks list": "rexecop.locks_list.v0.1",
    "observability diagnostics": "rexecop.runtime_diagnostics.v0.1",
    "observability logs list": "rexecop.structured_log_list.v0.1",
    "operation diff": "rexecop.operation_plan_diff.v0.1",
    "operation explain": "rexecop.operation_explain.v0.1",
    "operation review": "rexecop.operation_review.v0.1",
    "ops": "rexecop.ops.v0.1",
    "profile lint": "rexecop.profile_conformance.v0.1",
    "receipt show": "rexecop.receipt_show.v0.1",
    "reaction explain": "rexecop.reaction_explain.v0.1",
    "reaction-proposal-review": "rexecop.proposal_review.v0.1",
    "reaction-proposal-submit": "rexecop.proposal_submission.v0.1",
    "runtime reconstruct-status": "rexecop.runtime_reconstruction.v0.1",
    "runtime status": "rexecop.runtime_status.v0.1",
    "status": "rexecop.operation_status.v0.1",
    "support bundle": "rexecop.support_bundle.v0.1",
}

EXPECTED_COMMAND_GROUPS = {
    "audit_inspection": {
        "chain explain",
        "chain summary",
        "evidence show",
        "reaction explain",
        "reaction-proposal-review",
        "reaction-proposal-submit",
        "receipt show",
        "support bundle",
    },
    "operation_inspection": {
        "operation diff",
        "operation explain",
        "operation review",
        "status",
    },
    "observability": {
        "observability diagnostics",
        "observability logs list",
    },
    "profile_developer": {"profile lint"},
    "governance_inspection": {"governance controls"},
    "runtime_triage": {
        "dead-letter list",
        "dead-letter show",
        "explain-error",
        "locks list",
        "ops",
        "runtime reconstruct-status",
        "runtime status",
    },
}

EXPECTED_OUTPUT_POLICIES = {
    "operation diff": "format_option",
    "operation review": "format_option",
    "runtime reconstruct-status": "json_only_flag",
    "runtime status": "json_only_flag",
}


def test_cli_contract_registry_is_valid_and_snapshot_stable() -> None:
    registry = cli_contract_registry()

    assert registry["schema"] == CLI_CONTRACT_REGISTRY_SCHEMA
    assert validate_cli_contract_registry(registry) == []
    assert {
        item["command"]: item["schema"] for item in registry["contracts"]
    } == EXPECTED_COMMAND_SCHEMAS
    assert {
        item["group"]: set(item["commands"]) for item in registry["command_groups"]
    } == EXPECTED_COMMAND_GROUPS
    assert registry["contract_count"] == len(EXPECTED_COMMAND_SCHEMAS)
    for item in registry["contracts"]:
        assert item["default_format"] in item["formats"]
        assert item["output_policy"] == EXPECTED_OUTPUT_POLICIES.get(
            item["command"], "json_only"
        )
        assert item["authority"]
        assert item["redacted"] is True
        assert item["bounded_output"] is True
        assert item["error_schema"] == CLI_ERROR_SCHEMA
        assert any(code["code"] == 0 for code in item["exit_codes"])
    assert {
        item["command"]: item["output_policy"] for item in registry["format_matrix"]
    } == {
        command: EXPECTED_OUTPUT_POLICIES.get(command, "json_only")
        for command in EXPECTED_COMMAND_SCHEMAS
    }
    assert {
        item["command"]: item["error_schema"] for item in registry["exit_code_matrix"]
    } == {command: CLI_ERROR_SCHEMA for command in EXPECTED_COMMAND_SCHEMAS}


def test_contracts_cli_outputs_registry() -> None:
    result = runner.invoke(app, ["contracts", "cli"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload == cli_contract_registry()
    assert payload["schema"] == CLI_CONTRACT_REGISTRY_SCHEMA


def test_cli_contract_registry_validator_rejects_missing_schema() -> None:
    payload = cli_contract_registry()
    payload["contracts"][0]["schema"] = ""

    errors = validate_cli_contract_registry(payload)

    assert any(item.endswith(":schema") for item in errors)
