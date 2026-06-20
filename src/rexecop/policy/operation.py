from __future__ import annotations

from govengine import PolicyEngine
from govengine.policy.compiler import CompiledPolicyPack
from govengine.policy.model import PolicyVerdict

from rexecop.adapters.govengine_port.contracts import is_mutating_mode
from rexecop.connectors.errors import READ_ONLY_MODES
from rexecop.environment.model import Environment
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
