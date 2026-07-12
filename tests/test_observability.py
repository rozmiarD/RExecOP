from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.evidence.redaction import register_secret_value
from rexecop.observability.diagnostics import (
    RUNTIME_DIAGNOSTICS_SCHEMA,
    collect_runtime_diagnostics,
)
from rexecop.observability.emitter import StructuredLogEmitter
from rexecop.observability.failure_classes import FAILURE_CLASSES
from rexecop.observability.structured_log import (
    STRUCTURED_LOG_EVENT_SCHEMA,
    STRUCTURED_LOG_LIST_SCHEMA,
    StructuredLogRefs,
    build_structured_log_event,
    list_structured_logs,
)
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def test_structured_log_event_schema_and_redaction() -> None:
    register_secret_value("fixture-observability-secret")
    event = build_structured_log_event(
        event_kind="plan_recorded",
        correlation_id="corr-1",
        message="logged fixture-observability-secret value",
        refs=StructuredLogRefs(
            operation_id="op-1",
            plan_id="op-1",
            evidence_ref="ev-1",
        ),
        details={"note": "fixture-observability-secret"},
    )

    assert event["schema"] == STRUCTURED_LOG_EVENT_SCHEMA
    assert event["audience"] == "runtime_diagnostic"
    assert event["refs"]["operation_id"] == "op-1"
    assert event["refs"]["plan_id"] == "op-1"
    assert event["refs"]["evidence_ref"] == "ev-1"
    assert "fixture-observability-secret" not in event["message"]
    assert "fixture-observability-secret" not in json.dumps(event["details"])


def test_structured_log_rejects_unknown_failure_class() -> None:
    with pytest.raises(ValueError, match="unsupported failure_class"):
        build_structured_log_event(
            event_kind="runtime_failure",
            correlation_id="corr-1",
            message="failed",
            failure_class="unknown-class",
        )


def test_structured_log_list_filters_by_operation_and_correlation(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runtime")
    store.ensure_layout()
    emitter = StructuredLogEmitter(store)
    emitter.emit(
        event_kind="plan_recorded",
        correlation_id="corr-a",
        message="plan a",
        refs=StructuredLogRefs(operation_id="op-a", plan_id="op-a"),
    )
    emitter.emit(
        event_kind="plan_recorded",
        correlation_id="corr-b",
        message="plan b",
        refs=StructuredLogRefs(operation_id="op-b", plan_id="op-b"),
    )

    by_operation = list_structured_logs(store, operation_id="op-a")
    assert by_operation["schema"] == STRUCTURED_LOG_LIST_SCHEMA
    assert by_operation["count"] == 1
    assert by_operation["items"][0]["refs"]["operation_id"] == "op-a"

    by_correlation = list_structured_logs(store, correlation_id="corr-b")
    assert by_correlation["count"] == 1
    assert by_correlation["items"][0]["correlation_id"] == "corr-b"


def test_plan_emits_structured_logs_with_refs(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runtime")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )

    logs = list_structured_logs(store, operation_id=operation.id)
    kinds = {item["event_kind"] for item in logs["items"]}

    assert "plan_recorded" in kinds
    assert any(item["event_kind"] == "evidence_recorded" for item in logs["items"])
    plan_event = next(item for item in logs["items"] if item["event_kind"] == "plan_recorded")
    assert plan_event["correlation_id"] == operation.correlation_id
    assert plan_event["refs"]["operation_id"] == operation.id
    assert plan_event["refs"]["plan_id"] == operation.id
    assert plan_event["refs"]["evidence_ref"]


def test_runtime_diagnostics_use_explain_error_failure_classes(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runtime")
    store.ensure_layout()
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    from rexecop.operation.model import Operation

    store.save_operation(
        Operation(
            id="op-failed-diagnostics",
            profile="runtime-fixture",
            environment="runtime-fixture",
            intent="inspect_fixture_state",
            target="fixture-target",
            mode="dry_run",
            requested_by="operator",
            state=OperationState.FAILED.value,
            created_at=now,
            updated_at=now,
            correlation_id="corr-diagnostics",
            metadata={
                "policy_verdict": {
                    "decision": "deny",
                    "reason_code": "policy_blocked",
                }
            },
        )
    )

    payload = collect_runtime_diagnostics(store)

    assert payload["schema"] == RUNTIME_DIAGNOSTICS_SCHEMA
    assert set(payload["failure_classes"]) == set(FAILURE_CLASSES)
    assert payload["blockers"]
    assert payload["blockers"][0]["failure_class"] == "policy"
    assert "secret values" in " ".join(payload["non_claims"])


def test_cli_observability_commands(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    controller = OperationController(store=FileStore(root))
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )

    logs = runner.invoke(
        app,
        ["--root", str(root), "observability", "logs", "list", "--operation", operation.id],
    )
    assert logs.exit_code == 0, logs.output
    logs_payload = json.loads(logs.stdout)
    assert logs_payload["schema"] == STRUCTURED_LOG_LIST_SCHEMA
    assert logs_payload["count"] >= 1

    diagnostics = runner.invoke(app, ["--root", str(root), "observability", "diagnostics"])
    assert diagnostics.exit_code == 0, diagnostics.output
    diagnostics_payload = json.loads(diagnostics.stdout)
    assert diagnostics_payload["schema"] == RUNTIME_DIAGNOSTICS_SCHEMA
    assert diagnostics_payload["structured_logs"]["count"] >= 1
