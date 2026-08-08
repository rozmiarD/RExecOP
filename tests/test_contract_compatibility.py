from __future__ import annotations

from pathlib import Path

import govengine
import pytest
from govengine import validate_supported_contract_version

from rexecop.runtime import doctor
from rexecop.runtime.contract_compatibility import (
    STACK_CONTRACT_COMPATIBILITY_SCHEMA,
    evaluate_govengine_contract_compatibility,
    evaluate_stack_contract_compatibility,
    rexecop_runtime_projection_matrix,
    validate_sclite_artifact_pins,
)
from rexecop.runtime.doctor import run_runtime_doctor


def _patch_stack_package_versions(
    monkeypatch: pytest.MonkeyPatch,
    *,
    govengine_version: str,
    sclite_version: str,
) -> None:
    versions = {
        "govengine": govengine_version,
        "sclite-core": sclite_version,
    }
    monkeypatch.setattr(doctor.metadata, "version", versions.__getitem__)


def test_doctor_accepts_exact_release_train_package_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stack_package_versions(
        monkeypatch,
        govengine_version="1.0.0rc2",
        sclite_version="2.0.1",
    )

    check = doctor._check_stack_packages()

    assert check["status"] == "passed"
    assert check["details"]["found"] == {
        "rexecop": "1.0.0rc2",
        "govengine": "1.0.0rc2",
        "sclite-core": "2.0.1",
    }


@pytest.mark.parametrize(
    ("govengine_version", "sclite_version", "expected_mismatch"),
    [
        ("1.0.0rc1", "2.0.1", "govengine:1.0.0rc1!=1.0.0rc2"),
        ("1.0.0rc2", "2.0.0", "sclite-core:2.0.0!=2.0.1"),
    ],
)
def test_doctor_rejects_stale_release_train_package(
    monkeypatch: pytest.MonkeyPatch,
    govengine_version: str,
    sclite_version: str,
    expected_mismatch: str,
) -> None:
    _patch_stack_package_versions(
        monkeypatch,
        govengine_version=govengine_version,
        sclite_version=sclite_version,
    )

    check = doctor._check_stack_packages()

    assert check["status"] == "blocker"
    assert expected_mismatch in check["details"]["mismatches"]


def test_rexecop_runtime_projection_matrix_lists_execution_surfaces() -> None:
    matrix = rexecop_runtime_projection_matrix()

    assert matrix["schema"] == STACK_CONTRACT_COMPATIBILITY_SCHEMA
    surface_ids = {item["surface_id"] for item in matrix["projections"]}
    assert "step_execution_spec" in surface_ids
    assert "execution_request" in surface_ids
    assert "execution_receipt" in surface_ids


def test_evaluate_govengine_contract_compatibility_passes() -> None:
    result = evaluate_govengine_contract_compatibility()

    assert result["status"] == "passed"
    assert result["matched_contracts"]
    assert result["govengine_contract_catalog"]["contracts"]
    assert result["optional_surface_status"] == "available"
    assert result["optional_contracts"] == [
        {
            "surface_id": "typed_execution_governed_admission",
            "schema_version": "v0.1",
            "status": "supported",
        },
        {
            "surface_id": "typed_execution_governed_admission",
            "schema_version": "v0.2",
            "status": "supported",
        },
    ]


def test_missing_optional_governed_surface_does_not_claim_availability(
    monkeypatch,
) -> None:
    catalog = govengine.supported_contract_report()
    catalog["contracts"] = [
        item
        for item in catalog["contracts"]
        if item["surface_id"] != "typed_execution_governed_admission"
    ]
    monkeypatch.setattr(
        govengine,
        "supported_contract_report",
        lambda: catalog,
    )

    result = evaluate_govengine_contract_compatibility()

    assert result["status"] == "passed"
    assert result["optional_surface_status"] == "unavailable"
    assert {item["status"] for item in result["optional_contracts"]} == {
        "unavailable"
    }


def test_validate_supported_contract_version_blocks_unknown_major() -> None:
    try:
        validate_supported_contract_version("typed_execution_governance_request", "v9.0")
        raised = False
    except Exception:
        raised = True

    assert raised


def test_evaluate_stack_contract_compatibility_passes() -> None:
    result = evaluate_stack_contract_compatibility()

    assert result["status"] == "passed"
    assert not result["blockers"]
    assert not validate_sclite_artifact_pins()


def test_runtime_doctor_includes_stack_contract_compatibility(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "runtime_manifest.json").write_text("{}\n", encoding="utf-8")
    for relative in (
        "operations",
        "plans",
        "evidence",
        "receipts",
        "sclite",
        "approvals",
        "queue",
    ):
        (root / relative).mkdir(parents=True)
    (root / "queue" / "run_now.json").write_text("[]\n", encoding="utf-8")

    report = run_runtime_doctor(root)

    check = next(item for item in report["checks"] if item["id"] == "stack_contract_compatibility")
    assert check["status"] == "passed"
    assert report["schema"] == "rexecop.doctor_report.v0.1"
    assert report["contract_versions"]["status"] == "passed"
