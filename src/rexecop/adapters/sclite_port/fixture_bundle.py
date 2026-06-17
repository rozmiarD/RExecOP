from __future__ import annotations

from pathlib import Path
from typing import Any

from rexecop.adapters.sclite_port.full_bundle import (
    KERNEL_GUARD_MANIFEST_FILE,
    write_kernel_guard_manifest,
)
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan

# Test/lab-only HMAC key for kernel_guard fixtures. Never ship as production default.
REXECOP_FIXTURE_GUARD_KEY = "rexecop-fixture-guard-key"
REXECOP_FIXTURE_GUARD_KEY_ID = "rexecop-fixture-guard-key"


def write_fixture_kernel_guard_manifest(bundle_dir: str | Path) -> dict[str, Any]:
    return write_kernel_guard_manifest(
        bundle_dir,
        key=REXECOP_FIXTURE_GUARD_KEY,
        key_id=REXECOP_FIXTURE_GUARD_KEY_ID,
    )


def emit_fixture_operation_bundle(
    emitter: Any,
    *,
    operation: Operation,
    plan: OperationPlan,
    bundle_dir: str,
    evidence_events: list[dict[str, Any]] | None = None,
) -> Any:
    """Emit a review bundle and attach the public fixture kernel-guard sidecar (tests/lab)."""
    result = emitter.emit_operation_bundle(
        operation=operation,
        plan=plan,
        bundle_dir=bundle_dir,
        evidence_events=evidence_events,
    )
    write_fixture_kernel_guard_manifest(result.bundle_dir)
    guard_path = Path(result.bundle_dir) / KERNEL_GUARD_MANIFEST_FILE
    if guard_path.is_file():
        result.sclite_refs["kernel_guard_manifest"] = {
            "sclite_schema_ref": "schemas/kernel_guard_hmac_v1.schema.json",
            "descriptor_path": f"{result.bundle_dir}/{KERNEL_GUARD_MANIFEST_FILE}",
            "status": "emitted",
        }
    return result
