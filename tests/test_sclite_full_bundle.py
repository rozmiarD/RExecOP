from __future__ import annotations

import json
from pathlib import Path

from sclite.artifacts import validate_artifact
from sclite.bundles import review_bundle
from sclite.integrity import artifact_descriptor

from rexecop.adapters.sclite_port.emitter import SCLiteArtifactEmitter
from rexecop.adapters.sclite_port.fixture_bundle import emit_fixture_operation_bundle
from rexecop.adapters.sclite_port.full_bundle import (
    CARRIER_PROFILE_REF_FILE,
    FULL_BUNDLE_SIDECARS,
    KERNEL_GUARD_MANIFEST_FILE,
    TRUST_PROFILE_REF_FILE,
)
from rexecop.adapters.sclite_port.target_host import resolve_sclite_target_host
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan


def _sample_operation() -> Operation:
    return Operation(
        id="op-test-001",
        profile="tecrax-fixture",
        environment="small-public-unit",
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
        requested_by="operator",
        state="planned",
        created_at="2026-06-16T12:00:00+00:00",
        updated_at="2026-06-16T12:00:01+00:00",
        correlation_id="corr-1",
    )


def _sample_plan() -> OperationPlan:
    return OperationPlan(
        operation_id="op-test-001",
        profile="tecrax-fixture",
        environment="small-public-unit",
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
        workflow={"id": "check_backup_status"},
        planned_steps=[
            {
                "id": "resolve_inventory",
                "type": "internal",
                "action": "resolve_inventory",
            },
            {
                "id": "query_pbs",
                "type": "connector",
                "connector": "pbs",
                "action": "list_snapshots",
            },
        ],
        required_connectors=["pbs"],
        risk="low",
        govengine_request_preview={},
        expected_evidence=["plan_generated"],
        pause_safe_points=[],
        retry_policy_summary={"max_attempts": 0},
        rollback_available=False,
    )


def test_full_bundle_review_verdict_pass(tmp_path: Path) -> None:
    emitter = SCLiteArtifactEmitter()
    bundle_dir = tmp_path / "bundle"
    result = emitter.emit_operation_bundle(
        operation=_sample_operation(),
        plan=_sample_plan(),
        bundle_dir=str(bundle_dir),
    )
    assert result.review_record["verdict"] == "pass"
    assert result.bundle_profile == "govengine_integration_v0.5"
    assert result.review_record["summary"]["scope_fidelity_verdict"] == "pass"
    assert result.review_record["summary"]["ticket_use_status"] == "pass"


def test_full_bundle_sidecars_and_kernel_guard(tmp_path: Path) -> None:
    emitter = SCLiteArtifactEmitter()
    bundle_dir = tmp_path / "bundle"
    result = emit_fixture_operation_bundle(
        emitter,
        operation=_sample_operation(),
        plan=_sample_plan(),
        bundle_dir=str(bundle_dir),
    )
    base = Path(bundle_dir)
    for filename in FULL_BUNDLE_SIDECARS:
        assert (base / filename).is_file()
    assert (base / KERNEL_GUARD_MANIFEST_FILE).is_file()
    trust = json.loads((base / TRUST_PROFILE_REF_FILE).read_text(encoding="utf-8"))
    carrier = json.loads((base / CARRIER_PROFILE_REF_FILE).read_text(encoding="utf-8"))
    validate_artifact(trust, "trust_profile_ref.v0.1")
    validate_artifact(carrier, "carrier_profile_ref.v0.1")
    ticket_digest = artifact_descriptor(result.artifacts["execution_ticket"])["digest"]
    assert trust["integrity"]["subject_artifact_digest"] == ticket_digest
    assert carrier["integrity"]["subject_artifact_digest"] == ticket_digest
    assert result.sidecars is not None
    assert set(result.sidecars) == {TRUST_PROFILE_REF_FILE, CARRIER_PROFILE_REF_FILE}


def test_full_bundle_scoped_ticket_v03_and_receipt_bounded_evidence(tmp_path: Path) -> None:
    emitter = SCLiteArtifactEmitter()
    bundle_dir = tmp_path / "bundle"
    result = emitter.emit_operation_bundle(
        operation=_sample_operation(),
        plan=_sample_plan(),
        bundle_dir=str(bundle_dir),
    )
    ticket = result.artifacts["execution_ticket"]
    receipt = result.artifacts["execution_receipt"]
    evidence = result.artifacts["evidence_contract"]
    assert ticket["schema_version"] == "v0.3"
    assert ticket["ticket_profile"] == "scoped_execution_ticket"
    assert receipt["ticket_use"]["ticket_id"] == ticket["ticket_id"]
    assert evidence["claims"][0]["bounded_by_receipt"] is True
    assert evidence["claims"][0]["source_receipt_id"] == receipt["receipt_id"]


def test_target_host_resolution_for_logical_targets() -> None:
    plan = _sample_plan()
    host = resolve_sclite_target_host(plan)
    assert host == "small-public-unit.fixture"
    assert "." in host


def test_review_bundle_matches_sclite_govengine_integration_shape(tmp_path: Path) -> None:
    emitter = SCLiteArtifactEmitter()
    bundle_dir = tmp_path / "bundle"
    emitter.emit_operation_bundle(
        operation=_sample_operation(),
        plan=_sample_plan(),
        bundle_dir=str(bundle_dir),
    )
    record = review_bundle(bundle_dir)
    check_names = {item["name"] for item in record["checks"]}
    assert check_names == {
        "schema_validation",
        "chain_integrity",
        "lifecycle_binding",
        "scope_fidelity",
        "ticket_use_profile",
    }
    assert record["verdict"] == "pass"
