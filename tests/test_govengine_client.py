from __future__ import annotations

from typing import Any

import govengine
from govengine import compose_runtime_admission_result

from rexecop.adapters.govengine_port.client import (
    GovEngineClient,
    build_compose_inputs,
    build_runner_request_preview,
    map_runtime_admission_to_decision,
)
from rexecop.adapters.govengine_port.contracts import (
    GovEngineDecisionType,
    GovEngineRequest,
)


def test_govengine_imports() -> None:
    assert govengine.__version__


def test_compose_runtime_admission_allowed() -> None:
    result = compose_runtime_admission_result(
        admission_id="adm-test",
        subject_ref="rexecop:test",
        prepared_execution_contract={"status": "prepared", "digest": "sha256:contract"},
        policy_decision={"decision": "allow", "policy_id": "policy-1"},
        execution_ticket={"status": "passed", "ticket_id": "ticket-1", "digest": "sha256:ticket"},
        trust_decision={"status": "passed", "trust_status": "trusted", "verifier_id": "fixture"},
        runner_profile={"name": "rexecop", "allowed": True, "live_backend_enabled": True},
        receipt_obligation={"required": True, "binds": ["admission", "ticket"]},
        live=True,
    )
    decision = map_runtime_admission_to_decision(result)
    assert decision.decision_type == GovEngineDecisionType.ALLOWED


def test_govengine_client_fail_closed_without_policy() -> None:
    client = GovEngineClient()
    decision = client.evaluate(
        GovEngineRequest(
            operation_id="op-test",
            profile="tecrax",
            environment="env",
            intent="check_backup_status",
            target="all_critical_vms",
            mode="apply",
            risk="low",
            preview={"note": "preview only"},
        )
    )
    assert decision.decision_type in {
        GovEngineDecisionType.BLOCKED,
        GovEngineDecisionType.APPROVAL_REQUIRED,
    }


def test_build_runner_request_preview_shape() -> None:
    preview = build_runner_request_preview(
        [{"type": "connector", "connector": "pbs", "action": "list_snapshots"}],
        operation_id="op-1",
    )
    assert preview["request_id"] == "req-op-1"
    assert preview["dry_run"] is True
    assert preview["steps"]


def _allowed_compose_inputs(operation_id: str) -> dict[str, Any]:
    return {
        "admission_id": f"adm-{operation_id}",
        "subject_ref": f"rexecop:{operation_id}",
        "prepared_execution_contract": {"status": "prepared", "digest": "sha256:contract"},
        "policy_decision": {"decision": "allow", "policy_id": "policy-1"},
        "execution_ticket": {
            "status": "passed",
            "ticket_id": "ticket-1",
            "digest": "sha256:ticket",
        },
        "trust_decision": {"status": "passed", "trust_status": "trusted", "verifier_id": "fixture"},
        "runner_profile": {"name": "rexecop", "allowed": True, "live_backend_enabled": True},
        "receipt_obligation": {"required": True, "binds": ["admission", "ticket"]},
    }


def test_build_compose_inputs_uses_override() -> None:
    override = _allowed_compose_inputs("op-override")
    inputs = build_compose_inputs(
        GovEngineRequest(
            operation_id="op-override",
            profile="tecrax",
            environment="env",
            intent="x",
            target="t",
            mode="apply",
            risk="low",
            preview={"admission_compose": override},
        )
    )
    assert inputs["admission_id"] == "adm-op-override"
