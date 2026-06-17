from __future__ import annotations

from rexecop.adapters.sclite_port.emitter import _rexecop_mode
from rexecop.adapters.sclite_port.execution_receipt_metrics import (
    derive_execution_receipt_metrics,
    receipt_non_claims,
)
from rexecop.adapters.sclite_port.full_bundle import build_scoped_execution_receipt
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan


def _plan() -> OperationPlan:
    return OperationPlan(
        operation_id="op-metrics",
        profile="http-health-fixture",
        environment="local",
        intent="http_health_check",
        target="api_primary",
        mode="dry_run",
        workflow={"id": "http.health_check"},
        planned_steps=[
            {"id": "probe", "type": "connector", "connector": "health", "action": "ping"},
            {"id": "produce_receipt", "type": "evidence", "action": "produce_receipt"},
        ],
        required_connectors=["health"],
        risk="low",
        govengine_request_preview={},
        expected_evidence=[],
        pause_safe_points=[],
        retry_policy_summary={},
        rollback_available=False,
    )


def _operation(*, mode: str = "dry_run") -> Operation:
    return Operation(
        id="op-metrics",
        profile="http-health-fixture",
        environment="local",
        intent="http_health_check",
        target="api_primary",
        mode=mode,
        requested_by="operator",
        state="completed",
        created_at="2026-06-17T12:00:00+00:00",
        updated_at="2026-06-17T12:00:05+00:00",
        metadata={
            "shared_state": {
                "connector_results": {
                    "probe": {"status": "ok", "http_status": 200},
                }
            }
        },
    )


def test_derive_execution_receipt_metrics_from_shared_state() -> None:
    count, network = derive_execution_receipt_metrics(
        _operation(),
        _plan(),
        rexecop_mode=_rexecop_mode,
    )
    assert count == 1
    assert network is False


def test_derive_execution_receipt_metrics_from_step_completed_events() -> None:
    operation = _operation()
    operation.metadata = {}
    evidence = [
        {
            "event_type": "step_completed",
            "step_id": "probe",
            "sanitized_payload": {"step_id": "probe", "success": True},
        }
    ]
    count, network = derive_execution_receipt_metrics(
        operation,
        _plan(),
        evidence_events=evidence,
        rexecop_mode=_rexecop_mode,
    )
    assert count == 1
    assert network is False


def test_network_execution_performed_in_apply_mode() -> None:
    plan = _plan()
    plan.mode = "apply"
    count, network = derive_execution_receipt_metrics(
        _operation(mode="apply"),
        plan,
        rexecop_mode=_rexecop_mode,
    )
    assert count == 1
    assert network is True


def test_receipt_non_claims_preserved_for_dry_run() -> None:
    claims = receipt_non_claims("dry_run", network_execution_performed=False)
    assert "receipt_does_not_claim_live_target_execution" in claims


def test_receipt_non_claims_omit_live_claim_when_network_performed() -> None:
    claims = receipt_non_claims("live", network_execution_performed=True)
    assert "receipt_does_not_claim_live_target_execution" not in claims


def test_build_scoped_execution_receipt_uses_derived_metrics() -> None:
    plan = _plan()
    operation = _operation()
    contract = {
        "execution_shape": {"tool": "health", "normalized_args": [], "plan": []},
    }
    ticket = {"ticket_id": "scoped-ticket-op-metrics"}

    def steps(_plan: OperationPlan) -> list[dict[str, object]]:
        return [{"step": 1, "tool": "health", "args": ["ping"]}]

    def link(_role: str, _artifact: dict[str, object]) -> dict[str, str]:
        return {"role": "execution_contract", "descriptor": {}}

    def validate(_role: str, artifact: dict[str, object]) -> dict[str, object]:
        return artifact

    receipt = build_scoped_execution_receipt(
        operation,
        plan,
        contract,
        ticket,
        completed_at=None,
        rexecop_mode=_rexecop_mode,
        execution_plan_steps=steps,
        link=link,
        validate=validate,
        evidence_events=[],
    )
    assert receipt["execution"]["executed_command_count"] == 1
    assert receipt["execution"]["network_execution_performed"] is False
    assert "receipt_does_not_claim_live_target_execution" in receipt["non_claims"]
