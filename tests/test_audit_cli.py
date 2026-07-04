from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.evidence.event import EvidenceEventType
from rexecop.operation.controller import OperationController
from rexecop.storage.file_store import FileStore

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def _planned_operation(tmp_path: Path):
    root = tmp_path / ".rexecop"
    store = FileStore(root)
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    return root, controller, operation


def _exported_operation(tmp_path: Path):
    root, controller, operation = _planned_operation(tmp_path)
    controller.export_receipt(operation.id)
    return root, controller, controller.get_operation(operation.id)


def _invoke(root: Path, *args: str):
    return runner.invoke(app, ["--root", str(root), *args])


def _json_output(result) -> dict:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_receipt_show_reports_redacted_sclite_refs(tmp_path: Path) -> None:
    root, _, operation = _exported_operation(tmp_path)

    result = _invoke(root, "receipt", "show", operation.id)
    payload = _json_output(result)

    assert payload["schema"] == "rexecop.receipt_show.v0.1"
    assert payload["status"] == "present"
    assert payload["receipt_export"]["authority"] == "sclite_artifact"
    assert payload["broken_artifacts"] == []
    artifacts = payload["sclite_refs"]["artifacts"]
    assert any(item["role"] == "execution_receipt" for item in artifacts)
    assert all(not str(item["path"]).startswith(str(tmp_path)) for item in artifacts)
    assert all("actual_digest" in item for item in artifacts)


def test_receipt_show_fails_closed_on_broken_digest(tmp_path: Path) -> None:
    root, _, operation = _exported_operation(tmp_path)
    intent_ref = operation.sclite_refs["intent_contract"]
    intent_path = Path(intent_ref["descriptor_path"])
    payload = json.loads(intent_path.read_text(encoding="utf-8"))
    payload["intent"]["action"] = "tampered"
    intent_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    result = _invoke(root, "receipt", "show", operation.id)
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["schema"] == "rexecop.cli_error.v0.1"
    assert payload["error_class"] == "missing_artifact"
    assert payload["reason_code"] == "receipt_broken_digest"
    assert payload["details"]["status"] == "broken"
    assert "intent_contract" in payload["details"]["broken_artifacts"]


def test_receipt_show_reports_missing_artifact_without_crashing(tmp_path: Path) -> None:
    root, _, operation = _exported_operation(tmp_path)
    receipt_ref = operation.sclite_refs["execution_receipt"]
    Path(receipt_ref["descriptor_path"]).unlink()

    result = _invoke(root, "receipt", "show", operation.id)
    payload = _json_output(result)
    assert payload["status"] == "partial"
    assert "execution_receipt" in payload["missing_artifacts"]


def test_evidence_show_bounds_and_redacts_payloads(tmp_path: Path) -> None:
    root, controller, operation = _planned_operation(tmp_path)
    controller.evidence.emit(
        operation_id=operation.id,
        event_type=EvidenceEventType.STEP_COMPLETED,
        payload={
            "api_token": "fixture-secret-value",
            "nested": {"notes": "safe diagnostic text"},
        },
    )

    result = _invoke(root, "evidence", "show", operation.id)
    payload = _json_output(result)

    assert payload["schema"] == "rexecop.evidence_show.v0.1"
    assert payload["event_count"] >= 1
    assert payload["sensitivity"]["redaction_marker_count"] >= 1
    assert payload["sensitivity"]["strong_secret_pattern_detected"] is False
    assert "fixture-secret-value" not in result.stdout
    assert "[REDACTED]" in result.stdout


def test_chain_summary_includes_operation_evidence_and_sclite_links(tmp_path: Path) -> None:
    root, _, operation = _exported_operation(tmp_path)

    result = _invoke(root, "chain", "summary", operation.id)
    payload = _json_output(result)

    assert payload["schema"] == "rexecop.chain_summary.v0.1"
    kinds = {item["kind"] for item in payload["links"]}
    assert "operation" in kinds
    assert any(kind.startswith("event:") for kind in kinds)
    assert any(kind.startswith("sclite_") for kind in kinds)
    assert payload["replay"]["event_count"] >= 1


def test_support_bundle_requires_redacted_and_includes_audit_sections(tmp_path: Path) -> None:
    root, _, operation = _exported_operation(tmp_path)

    unredacted = _invoke(root, "support", "bundle", operation.id)
    assert unredacted.exit_code == 1
    unredacted_payload = json.loads(unredacted.stdout)
    assert unredacted_payload["schema"] == "rexecop.cli_error.v0.1"
    assert unredacted_payload["reason_code"] == "support_bundle_unavailable"
    assert "requires --redacted" in unredacted_payload["message"]

    result = _invoke(root, "support", "bundle", operation.id, "--redacted")
    payload = _json_output(result)
    assert payload["schema"] == "rexecop.support_bundle.v0.1"
    assert payload["redacted"] is True
    assert payload["receipt"]["schema"] == "rexecop.receipt_show.v0.1"
    assert payload["evidence"]["schema"] == "rexecop.evidence_show.v0.1"
    assert payload["chain"]["schema"] == "rexecop.chain_summary.v0.1"
