from __future__ import annotations

from govengine import PolicyEngine
from govengine.policy.compiler import CompiledPolicyPack
from govengine.policy.model import PolicyVerdict

from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest, ConnectorResponse
from rexecop.connectors.errors import READ_ONLY_MODES


def evaluate_connector_policy(
    request: ConnectorRequest,
    policy_pack: CompiledPolicyPack,
    *,
    operation_id: str,
    backend: str,
    target_criticality: str,
) -> PolicyVerdict:
    action_mode = "read" if request.mode in READ_ONLY_MODES else "mutating"
    policy_request = {
        "request_id": f"connector-policy:{operation_id}:{request.connector}:{request.action}",
        "subject_ref": f"rexecop:{operation_id}:{request.connector}",
        "action": {
            "mode": action_mode,
            "category": "connector",
            "backend": backend,
            "connector": request.connector,
            "name": request.action,
        },
        "resource": {
            "target_ref": request.target,
            "criticality": target_criticality,
        },
        "context": {
            "operation_id": operation_id,
        },
    }
    return PolicyEngine().evaluate(policy_request, policy_pack)


def policy_verdict_allows_execution(verdict: PolicyVerdict) -> bool:
    return verdict.decision in {"allow", "allow_with_obligations"}


def connector_policy_blocked_response(
    request: ConnectorRequest,
    verdict: PolicyVerdict,
) -> ConnectorResponse:
    reason = verdict.reason_code or verdict.decision
    blockers = list(verdict.blockers) if verdict.blockers else [reason]
    return ConnectorResponse(
        connector=request.connector,
        action=request.action,
        success=False,
        error=f"connector policy denied: {reason}",
        data={
            "error_class": connector_errors.POLICY_DENIED,
            "policy_decision": verdict.decision,
            "policy_reason_code": reason,
            "policy_blockers": blockers,
        },
    )


def connector_policy_gate(
    request: ConnectorRequest,
    policy_pack: CompiledPolicyPack | None,
    *,
    operation_id: str,
    backend: str,
    target_criticality: str,
) -> ConnectorResponse | None:
    if policy_pack is None:
        return None
    verdict = evaluate_connector_policy(
        request,
        policy_pack,
        operation_id=operation_id,
        backend=backend,
        target_criticality=target_criticality,
    )
    if policy_verdict_allows_execution(verdict):
        return None
    return connector_policy_blocked_response(request, verdict)
