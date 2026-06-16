from __future__ import annotations

from typing import Any

from govengine import compose_runtime_admission_result, runtime_admission_public_summary
from govengine.admission import RuntimeAdmissionResult

from rexecop.adapters.govengine_port.contracts import (
    GovEngineDecision,
    GovEngineDecisionType,
    GovEngineRequest,
    is_mutating_mode,
)


def map_runtime_admission_to_decision(result: RuntimeAdmissionResult) -> GovEngineDecision:
    if result.allowed:
        return GovEngineDecision(
            decision_type=GovEngineDecisionType.ALLOWED,
            summary=result.reason_code,
            details={
                "admission_id": result.admission_id,
                "status": result.status,
                "public_summary": runtime_admission_public_summary(result),
            },
        )

    actions = set(result.required_next_actions)
    blockers = set(result.blockers)

    if result.status == "needs_review" or actions.intersection(
        {"approve_execution_ticket", "obtain_policy_decision"}
    ):
        return GovEngineDecision(
            decision_type=GovEngineDecisionType.APPROVAL_REQUIRED,
            summary=result.reason_code,
            details={
                "admission_id": result.admission_id,
                "status": result.status,
                "blockers": list(blockers),
                "required_next_actions": list(actions),
            },
        )

    if result.status == "dry_run_only" or "live_backend_disabled" in blockers:
        return GovEngineDecision(
            decision_type=GovEngineDecisionType.READ_ONLY_ONLY,
            summary=result.reason_code,
            details={"admission_id": result.admission_id, "status": result.status},
        )

    return GovEngineDecision(
        decision_type=GovEngineDecisionType.BLOCKED,
        summary=result.reason_code,
        details={
            "admission_id": result.admission_id,
            "status": result.status,
            "blockers": list(blockers),
            "required_next_actions": list(actions),
        },
    )


def build_compose_inputs(request: GovEngineRequest) -> dict[str, Any]:
    preview = dict(request.preview)
    override = preview.get("admission_compose")
    if isinstance(override, dict):
        return dict(override)

    policy_decision = preview.get("policy_decision")
    if policy_decision is None:
        return {
            "admission_id": f"adm-{request.operation_id}",
            "subject_ref": f"rexecop:{request.operation_id}",
            "prepared_execution_contract": {
                "status": "prepared",
                "digest": f"sha256:{request.operation_id}",
            },
            "policy_decision": None,
            "execution_ticket": None,
            "trust_decision": None,
            "runner_profile": {
                "name": "rexecop",
                "allowed": False,
                "live_backend_enabled": False,
            },
            "receipt_obligation": {"required": True, "binds": ["admission", "ticket"]},
            "metadata": {"source": "rexecop", "phase": "2b"},
        }

    runner_profile = preview.get(
        "runner_profile",
        {
            "name": "rexecop",
            "allowed": True,
            "live_backend_enabled": is_mutating_mode(request.mode),
        },
    )

    return {
        "admission_id": f"adm-{request.operation_id}",
        "subject_ref": f"rexecop:{request.operation_id}",
        "prepared_execution_contract": preview.get(
            "prepared_execution_contract",
            {"status": "prepared", "digest": f"sha256:{request.operation_id}"},
        ),
        "policy_decision": policy_decision,
        "execution_ticket": preview.get("execution_ticket"),
        "trust_decision": preview.get("trust_decision"),
        "runner_profile": runner_profile,
        "receipt_obligation": preview.get(
            "receipt_obligation",
            {"required": True, "binds": ["admission", "ticket"]},
        ),
        "artifact_refs": preview.get("artifact_refs"),
        "metadata": {"source": "rexecop", "operation_id": request.operation_id},
    }


class GovEngineClient:
    """Real GovEngine adapter using runtime admission composition."""

    def evaluate(self, request: GovEngineRequest) -> GovEngineDecision:
        inputs = build_compose_inputs(request)
        try:
            result = compose_runtime_admission_result(
                **inputs,
                live=is_mutating_mode(request.mode),
            )
        except Exception as exc:
            return GovEngineDecision(
                decision_type=GovEngineDecisionType.ERROR,
                summary=str(exc),
                details={"adapter": "govengine_client"},
            )
        return map_runtime_admission_to_decision(result)


def build_runner_request_preview(
    plan_steps: list[dict[str, Any]],
    *,
    operation_id: str,
) -> dict[str, Any]:
    """Shape helper for future runner execution (Phase 4+)."""
    from govengine.execution.runner_protocol import GovRunnerRequest, GovRunnerStep

    steps = tuple(
        GovRunnerStep(
            index=index,
            tool=str(step.get("connector") or step.get("type") or "internal"),
            args=(str(step.get("action") or ""),),
        )
        for index, step in enumerate(plan_steps)
    )
    request = GovRunnerRequest(
        request_id=f"req-{operation_id}",
        source="rexecop",
        steps=steps,
        dry_run=True,
    )
    return request.as_dict()
