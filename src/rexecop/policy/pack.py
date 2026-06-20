from __future__ import annotations

from typing import Any

from govengine import PolicyCompiler, policy_verdict_to_gov_policy_decision
from govengine.policy.compiler import CompiledPolicyPack

from rexecop.errors import RExecOpValidationError


def compile_environment_policy_pack(raw: dict[str, Any] | None) -> CompiledPolicyPack | None:
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise RExecOpValidationError("policy_pack must be a mapping")
    result = PolicyCompiler().compile(raw)
    if not result.ok or result.policy_pack is None:
        reason = result.reason_code or "policy_pack_compile_failed"
        raise RExecOpValidationError(f"invalid policy_pack: {reason}")
    return result.policy_pack


def policy_decision_from_verdict(verdict: Any) -> dict[str, Any]:
    return policy_verdict_to_gov_policy_decision(verdict).as_dict()
