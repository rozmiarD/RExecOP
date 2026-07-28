from __future__ import annotations

import json
from pathlib import Path

import pytest

from rexecop.errors import RExecOpValidationError
from rexecop.runtime.contract_compatibility import (
    evaluate_stack_contract_compatibility,
    validate_rexecop_projection_version,
    validate_sclite_artifact_pins,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "stack_contract_compatibility_golden.json"


def _golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_stack_contract_golden_matches_runtime_matrix() -> None:
    golden = _golden()
    report = evaluate_stack_contract_compatibility()

    assert report["status"] == "passed"
    assert report["compatibility_policy"] == golden["compatibility_policy"]
    assert not validate_sclite_artifact_pins()

    projection_ids = {item["surface_id"] for item in report["runtime_projections"]["projections"]}
    assert set(golden["required_runtime_projections"]).issubset(projection_ids)

    matched = set(report["govengine_contracts"]["matched_contracts"])
    assert set(golden["required_govengine_contracts"]).issubset(matched)
    optional_contracts = {
        item["surface_id"]
        for item in report["govengine_contracts"]["optional_contracts"]
        if item["status"] == "supported"
    }
    assert set(golden["optional_govengine_contracts"]).issubset(optional_contracts)
    assert set(golden["optional_runtime_projections"]).issubset(projection_ids)

    sclite_versions = {
        item["role"]: item["schema_version"] for item in report["sclite_artifact_refs"]
    }
    for role, version in golden["sclite_artifact_versions"].items():
        assert sclite_versions[role] == version


def test_unknown_major_runtime_projection_fail_closed() -> None:
    with pytest.raises(
        RExecOpValidationError,
        match="unsupported_runtime_projection_major_version",
    ):
        validate_rexecop_projection_version("execution_request", "v9.0")
