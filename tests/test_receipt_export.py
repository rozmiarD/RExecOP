from __future__ import annotations

from pathlib import Path

import pytest

from rexecop.adapters.sclite_port.contracts import (
    ARTIFACT_SLOTS,
    RECEIPT_EXPORT_AUTHORITY,
    SCLITE_ARTIFACT_AUTHORITY,
)
from rexecop.operation.controller import OperationController
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"


def test_export_placeholder_receipt_writes_non_authoritative_file(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )
    with pytest.warns(DeprecationWarning, match="export_placeholder_receipt is deprecated"):
        result = controller.export_placeholder_receipt(operation.id)
    export = result["export"]
    assert isinstance(export, dict)
    assert export["authority"] == RECEIPT_EXPORT_AUTHORITY
    assert export["emitter"] == "placeholder"
    for role in ARTIFACT_SLOTS:
        assert export["artifact_slots"][role]["sclite_schema_ref"].startswith("schemas/")

    saved = store.load_receipt_export(operation.id)
    assert saved["authority"] == RECEIPT_EXPORT_AUTHORITY

    reloaded = controller.get_operation(operation.id)
    assert reloaded.sclite_refs
    assert all(
        reloaded.sclite_refs[role]["status"] == "placeholder" for role in ARTIFACT_SLOTS
    )


def test_export_receipt_writes_sclite_bundle_and_descriptor_refs(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )
    result = controller.export_receipt(operation.id)
    export = result["export"]
    assert export["authority"] == SCLITE_ARTIFACT_AUTHORITY
    assert export["emitter"] == "sclite"
    refs = result["sclite_refs"]
    assert refs["intent_contract"]["status"] == "emitted"
    assert refs["execution_receipt"]["status"] == "emitted"
    saved = store.load_receipt_export(operation.id)
    assert saved["authority"] == SCLITE_ARTIFACT_AUTHORITY
