from __future__ import annotations

from pathlib import Path

from sclite.bundles import REVIEW_BUNDLE_REQUIRED_FILES, validate_review_bundle_shape

from rexecop.adapters.sclite_port.emitter import (
    SCLiteArtifactEmitter,
    build_intent_contract,
    build_lifecycle_artifacts,
)
from rexecop.operation.controller import OperationController
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def _sample_operation() -> Operation:
    return Operation(
        id="op-test-001",
        profile="runtime-fixture",
        environment="runtime-fixture",
        intent="inspect_fixture_state",
        target="fixture-target",
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
        profile="runtime-fixture",
        environment="runtime-fixture",
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
        workflow={"id": "inspect_fixture_state"},
        planned_steps=[
            {
                "id": "inspect_state",
                "type": "connector",
                "connector": "fixture_source",
                "action": "read_fixture_state",
            },
        ],
        required_connectors=["fixture_source"],
        risk="low",
        govengine_request_preview={},
        expected_evidence=["plan_generated"],
        pause_safe_points=[],
        retry_policy_summary={"max_attempts": 0},
        rollback_available=False,
    )


def test_build_intent_contract_validates() -> None:
    artifact = build_intent_contract(_sample_operation(), _sample_plan())
    assert artifact["artifact_type"] == "intent_contract"
    assert artifact["schema_ref"].endswith("intent_contract.v0.2.schema.json")


def test_build_lifecycle_artifacts_linked() -> None:
    artifacts = build_lifecycle_artifacts(_sample_operation(), _sample_plan())
    assert set(artifacts) == set(REVIEW_BUNDLE_REQUIRED_FILES)
    receipt = artifacts["execution_receipt"]
    assert receipt["links"]["execution_contract"]["descriptor"]["digest"]
    assert receipt["links"]["execution_ticket"]["descriptor"]["digest"]


def test_emit_operation_bundle_materializes_review_bundle(tmp_path: Path) -> None:
    emitter = SCLiteArtifactEmitter()
    bundle_dir = tmp_path / "bundle"
    result = emitter.emit_operation_bundle(
        operation=_sample_operation(),
        plan=_sample_plan(),
        bundle_dir=str(bundle_dir),
    )
    shape = validate_review_bundle_shape(bundle_dir)
    assert shape["status"] == "passed"
    assert result.sclite_refs["intent_contract"]["status"] == "emitted"
    assert result.sclite_refs["execution_receipt"]["digest"]


def test_plan_emits_intent_sclite_ref(tmp_path: Path) -> None:
    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    assert operation.sclite_refs["intent_contract"]["status"] == "emitted"
    intent_path = Path(operation.sclite_refs["intent_contract"]["descriptor_path"])
    assert intent_path.is_file()


def test_export_receipt_populates_intent_and_execution_receipt_refs(tmp_path: Path) -> None:
    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    result = controller.export_receipt(operation.id)
    refs = result["sclite_refs"]
    assert isinstance(refs, dict)
    assert refs["intent_contract"]["digest"]
    assert refs["execution_receipt"]["digest"]
    validate_review_bundle_shape(result["bundle_dir"])
