from __future__ import annotations

from pathlib import Path

from rexecop.adapters.sclite_port.emitter import SCLiteArtifactEmitter
from rexecop.adapters.sclite_port.fixture_bundle import (
    REXECOP_FIXTURE_GUARD_KEY,
    emit_fixture_operation_bundle,
    write_fixture_kernel_guard_manifest,
)
from rexecop.adapters.sclite_port.full_bundle import KERNEL_GUARD_MANIFEST_FILE
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan


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


def test_production_emit_skips_fixture_kernel_guard(tmp_path: Path) -> None:
    emitter = SCLiteArtifactEmitter()
    bundle_dir = tmp_path / "bundle"
    result = emitter.emit_operation_bundle(
        operation=_sample_operation(),
        plan=_sample_plan(),
        bundle_dir=str(bundle_dir),
    )
    assert not (bundle_dir / KERNEL_GUARD_MANIFEST_FILE).is_file()
    assert result.sclite_refs["kernel_guard_manifest"]["status"] == "not_required"


def test_fixture_emit_writes_public_fixture_guard(tmp_path: Path) -> None:
    emitter = SCLiteArtifactEmitter()
    bundle_dir = tmp_path / "bundle"
    result = emit_fixture_operation_bundle(
        emitter,
        operation=_sample_operation(),
        plan=_sample_plan(),
        bundle_dir=str(bundle_dir),
    )
    guard_path = bundle_dir / KERNEL_GUARD_MANIFEST_FILE
    assert guard_path.is_file()
    assert REXECOP_FIXTURE_GUARD_KEY in guard_path.read_text(encoding="utf-8")
    assert result.sclite_refs["kernel_guard_manifest"]["status"] == "emitted"


def test_fixture_guard_key_not_in_full_bundle_module() -> None:
    text = (
        Path(__file__).resolve().parents[1]
        / "src/rexecop/adapters/sclite_port/full_bundle.py"
    ).read_text(encoding="utf-8")
    assert "rexecop-fixture-guard-key" not in text


def test_write_fixture_kernel_guard_after_production_emit(tmp_path: Path) -> None:
    emitter = SCLiteArtifactEmitter()
    bundle_dir = tmp_path / "bundle"
    emitter.emit_operation_bundle(
        operation=_sample_operation(),
        plan=_sample_plan(),
        bundle_dir=str(bundle_dir),
    )
    write_fixture_kernel_guard_manifest(bundle_dir)
    assert (bundle_dir / KERNEL_GUARD_MANIFEST_FILE).is_file()
