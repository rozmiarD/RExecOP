from __future__ import annotations

import json

from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.cli_contracts import (
    CLI_CONTRACT_REGISTRY_SCHEMA,
    cli_contract_registry,
    validate_cli_contract_registry,
)

runner = CliRunner()

EXPECTED_COMMAND_SCHEMAS = {
    "chain summary": "rexecop.chain_summary.v0.1",
    "dead-letter list": "rexecop.dead_letter_list.v0.1",
    "dead-letter show": "rexecop.dead_letter_show.v0.1",
    "evidence show": "rexecop.evidence_show.v0.1",
    "explain-error": "rexecop.explain_error.v0.1",
    "locks list": "rexecop.locks_list.v0.1",
    "operation diff": "rexecop.operation_plan_diff.v0.1",
    "operation explain": "rexecop.operation_explain.v0.1",
    "operation review": "rexecop.operation_review.v0.1",
    "ops": "rexecop.ops.v0.1",
    "profile lint": "rexecop.profile_conformance.v0.1",
    "receipt show": "rexecop.receipt_show.v0.1",
    "runtime status": "rexecop.runtime_status.v0.1",
    "status": "rexecop.operation_status.v0.1",
    "support bundle": "rexecop.support_bundle.v0.1",
}


def test_cli_contract_registry_is_valid_and_snapshot_stable() -> None:
    registry = cli_contract_registry()

    assert registry["schema"] == CLI_CONTRACT_REGISTRY_SCHEMA
    assert validate_cli_contract_registry(registry) == []
    assert {
        item["command"]: item["schema"] for item in registry["contracts"]
    } == EXPECTED_COMMAND_SCHEMAS
    assert registry["contract_count"] == len(EXPECTED_COMMAND_SCHEMAS)
    for item in registry["contracts"]:
        assert item["default_format"] in item["formats"]
        assert item["authority"]
        assert item["redacted"] is True
        assert item["bounded_output"] is True
        assert any(code["code"] == 0 for code in item["exit_codes"])


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
