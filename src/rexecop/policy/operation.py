from __future__ import annotations

from govengine import PolicyEngine
from govengine.policy.compiler import CompiledPolicyPack
from govengine.policy.model import PolicyVerdict

from rexecop.adapters.govengine_port.contracts import is_mutating_mode
from rexecop.connectors.errors import READ_ONLY_MODES
from rexecop.environment.model import Environment
from rexecop.errors import RExecOpValidationError
from rexecop.policy.criticality import target_criticality


def evaluate_operation_policy(
    *,
    policy_pack: CompiledPolicyPack,
    operation_id: str,
    profile: str,
    environment: Environment,
    intent: str,
    target: str,
    mode: str,
    risk: str,
) -> PolicyVerdict:
    action_mode = "mutating" if is_mutating_mode(mode) else "read"
    if mode in READ_ONLY_MODES:
        action_mode = "read"
    request = {
        "request_id": f"op-policy:{operation_id}",
        "subject_ref": f"rexecop:{operation_id}",
        "action": {
            "mode": action_mode,
            "category": "operation",
            "intent": intent,
            "risk": risk,
        },
        "resource": {
            "target_ref": target,
            "criticality": target_criticality(environment, target),
        },
        "context": {
            "profile": profile,
            "environment": environment.id,
        },
    }
    return PolicyEngine().evaluate(request, policy_pack)


def operation_policy_allows_plan(
    verdict: PolicyVerdict,
    *,
    controls_enforced: bool = False,
) -> bool:
    if verdict.decision == "allow" and not verdict.obligations and not verdict.constraints:
        return True
    return controls_enforced and verdict.decision == "allow_with_obligations"


def operation_policy_blockers(verdict: PolicyVerdict) -> tuple[str, list[str]]:
    blockers = [
        f"unsupported_obligation:{item.obligation_id}:{item.kind}"
        for item in verdict.obligations
    ]
    blockers.extend(
        f"unsupported_constraint:{item.constraint_id}:{item.kind}"
        for item in verdict.constraints
    )
    if blockers:
        return "unsupported_policy_controls", blockers
    if verdict.decision == "allow_with_obligations":
        return "unsupported_policy_obligations", ["unfulfilled_policy_obligations"]
    reason = verdict.reason_code or verdict.decision
    return reason, list(verdict.blockers) if verdict.blockers else [reason]


def require_operation_policy_allows_plan(
    verdict: PolicyVerdict,
    *,
    controls_enforced: bool = False,
) -> None:
    if operation_policy_allows_plan(verdict, controls_enforced=controls_enforced):
        return
    reason, blockers = operation_policy_blockers(verdict)
    blocker_text = ",".join(blockers)
    raise RExecOpValidationError(
        f"operation policy denied: {reason}"
        + (f" blockers={blocker_text}" if blocker_text else "")
    )
