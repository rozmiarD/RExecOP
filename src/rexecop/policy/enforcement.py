from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from govengine import (
    GovAdmissionDecision,
    GovApiError,
    PolicyEnforcementPlan,
    admit_policy_execution,
    policy_enforcement_admission,
    policy_enforcement_admission_digest,
    policy_enforcement_plan_digest,
    validate_policy_enforcement_admission,
    validate_policy_enforcement_plan,
)
from govengine.policy.compiler import CompiledPolicyPack
from govengine.policy.model import PolicyVerdict

from rexecop.errors import RExecOpValidationError


def build_policy_enforcement_record(
    policy_pack: CompiledPolicyPack,
    verdict: PolicyVerdict,
) -> dict[str, Any]:
    plan = admit_policy_execution(policy_pack, verdict)
    if not plan.allowed:
        blockers = ",".join(plan.blockers)
        raise RExecOpValidationError(
            f"operation policy denied: {plan.reason_code}"
            + (f" blockers={blockers}" if blockers else "")
        )
    admission = policy_enforcement_admission(plan)
    return {
        "plan": plan.as_dict(),
        "plan_digest": policy_enforcement_plan_digest(plan),
        "admission": admission.as_dict(),
        "admission_digest": policy_enforcement_admission_digest(admission),
    }


def validate_policy_enforcement_record(
    value: Mapping[str, Any],
    *,
    policy_pack: CompiledPolicyPack,
    verdict: Mapping[str, Any] | PolicyVerdict,
    planned_steps: list[dict[str, Any]] | None = None,
    connectors: Mapping[str, Any] | None = None,
) -> tuple[PolicyEnforcementPlan, GovAdmissionDecision]:
    plan_raw = value.get("plan")
    plan_digest = str(value.get("plan_digest") or "").strip()
    admission_raw = value.get("admission")
    admission_digest = str(value.get("admission_digest") or "").strip()
    if (
        not isinstance(plan_raw, Mapping)
        or not plan_digest
        or not isinstance(admission_raw, Mapping)
        or not admission_digest
    ):
        raise RExecOpValidationError("policy enforcement binding is incomplete")
    try:
        plan = validate_policy_enforcement_plan(
            plan_raw,
            policy_pack=policy_pack,
            verdict=verdict,
        )
        admission = validate_policy_enforcement_admission(
            admission_raw,
            plan=plan,
        )
    except GovApiError as exc:
        raise RExecOpValidationError(exc.reason_code) from exc
    if plan_digest != policy_enforcement_plan_digest(plan):
        raise RExecOpValidationError("policy enforcement plan digest drift")
    if admission_digest != policy_enforcement_admission_digest(admission):
        raise RExecOpValidationError("policy enforcement admission digest drift")
    _validate_runtime_support(
        plan,
        planned_steps=list(planned_steps or []),
        connectors=connectors or {},
    )
    return plan, admission


def execution_policy_binding(
    plan: PolicyEnforcementPlan,
    admission: GovAdmissionDecision,
    *,
    plan_digest: str,
    admission_digest: str,
) -> dict[str, str]:
    return {
        "schema_version": plan.schema_version,
        "enforcement_plan_id": plan.plan_id,
        "enforcement_plan_digest": plan_digest,
        "admission_id": admission.decision_id,
        "admission_digest": admission_digest,
        "policy_pack_id": plan.policy_pack_id,
        "policy_pack_version": plan.policy_pack_version,
        "policy_pack_digest": plan.policy_pack_digest,
        "verdict_id": plan.verdict_id,
        "verdict_digest": plan.verdict_digest,
    }


def _validate_runtime_support(
    plan: PolicyEnforcementPlan,
    *,
    planned_steps: list[dict[str, Any]],
    connectors: Mapping[str, Any],
) -> None:
    controls = plan.controls
    if controls.max_steps and len(planned_steps) > controls.max_steps:
        raise RExecOpValidationError("policy max_steps is lower than planned workflow")
    if not controls.timeout_seconds:
        return
    supported_timeout_backends = {
        "http_api",
        "local_shell_readonly",
        "ssh_readonly",
    }
    for step in planned_steps:
        if str(step.get("type") or "") != "connector":
            continue
        connector = str(step.get("connector") or "")
        config = connectors.get(connector)
        backend = (
            str(config.get("backend") or config.get("mode") or "mock")
            if isinstance(config, Mapping)
            else ""
        )
        if backend not in supported_timeout_backends:
            raise RExecOpValidationError(
                f"policy timeout unsupported by connector backend: {backend or 'missing'}"
            )
