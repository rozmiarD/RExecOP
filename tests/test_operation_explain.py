from __future__ import annotations

from rexecop.operation.explain import explain_operation
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan


def test_operation_explain_reports_mutating_contract_completeness() -> None:
    operation = Operation(
        id="op-1",
        profile="fixture",
        environment="env",
        intent="apply",
        target="target",
        mode="apply",
        requested_by="operator",
        state="planned",
        created_at="2026-07-04T00:00:00+00:00",
        updated_at="2026-07-04T00:00:00+00:00",
        metadata={
            "policy_enforcement": {
                "plan": {
                    "plan_id": "plan-1",
                    "status": "ready",
                    "reason_code": "policy_controls_projected",
                    "blockers": [],
                },
                "plan_digest": "sha256:" + "1" * 64,
                "admission": {
                    "decision_id": "admission-1",
                    "outcome": "allowed",
                },
                "admission_digest": "sha256:" + "2" * 64,
            },
            "policy_verdict": {
                "decision": "allow",
                "reason_code": "apply_allowed",
                "blockers": [],
            },
        },
    )
    plan = OperationPlan(
        operation_id="op-1",
        profile="fixture",
        environment="env",
        intent="apply",
        target="target",
        mode="apply",
        workflow={
            "id": "fixture.apply",
            "rollback": {"mode": "dry_run", "steps": [{"id": "rollback"}]},
        },
        planned_steps=[
            {"id": "apply", "type": "connector", "connector": "fixture", "action": "apply"},
            {"id": "receipt", "type": "evidence", "action": "produce_receipt"},
        ],
        required_connectors=["fixture"],
        risk="medium",
        govengine_request_preview={"policy_decision": {"decision": "allow"}},
        expected_evidence=["plan_generated"],
        pause_safe_points=[],
        retry_policy_summary={},
        rollback_available=True,
    )

    payload = explain_operation(operation, plan)

    assert payload["runtime_controls"]["mutating"] is True
    assert payload["runtime_controls"]["rollback_available"] is True
    assert payload["runtime_controls"]["preflight_available"] is True
    assert payload["runtime_controls"]["postflight_available"] is True
    assert payload["runtime_controls"]["mutation_contract_complete"] is True
    assert payload["governance"]["policy_enforcement"]["plan_status"] == "ready"
