from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from govengine.policy import policy_pack_digest
from govengine.policy.compiler import CompiledPolicyPack

from rexecop.policy.pack import compile_environment_policy_pack

POLICY_PACK_LIFECYCLE_SCHEMA = "rexecop.policy_pack_lifecycle.v0.1"
POLICY_PACK_SOURCE = "environment.policy_pack"


def describe_policy_pack_lifecycle(
    raw: Mapping[str, Any] | None,
    compiled: CompiledPolicyPack | None = None,
) -> dict[str, Any]:
    """Return RExecOp's redacted lifecycle projection for an environment policy pack."""
    if not raw:
        return _base_lifecycle(status="absent")

    policy_pack = compiled or compile_environment_policy_pack(dict(raw))
    if policy_pack is None:
        return _base_lifecycle(status="absent")

    lifecycle = _base_lifecycle(status="compiled")
    lifecycle.update(
        {
            "policy_id": policy_pack.policy_id,
            "version": policy_pack.version,
            "govengine_schema_version": policy_pack.schema_version,
            "policy_pack_digest": policy_pack_digest(policy_pack),
            "rule_count": len(policy_pack.rules),
        }
    )
    lifecycle["stages"].update(
        {
            "declared": True,
            "compiled": True,
        }
    )
    return lifecycle


def bind_policy_pack_lifecycle(
    lifecycle: Mapping[str, Any],
    *,
    enforcement: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    bound = dict(lifecycle)
    stages = dict(bound.get("stages") or {})
    if bound.get("status") != "absent":
        stages["bound_to_operation"] = True
    if isinstance(enforcement, Mapping) and enforcement:
        stages["enforcement_projected"] = True
        bound["enforcement_binding"] = {
            "plan_digest": str(enforcement.get("plan_digest") or ""),
            "admission_digest": str(enforcement.get("admission_digest") or ""),
        }
    bound["stages"] = stages
    return bound


def _base_lifecycle(*, status: str) -> dict[str, Any]:
    return {
        "schema": POLICY_PACK_LIFECYCLE_SCHEMA,
        "status": status,
        "source": POLICY_PACK_SOURCE,
        "authority": {
            "compiler": "govengine.PolicyCompiler",
            "digest": "govengine.policy.policy_pack_digest",
            "policy_reasoning": "govengine.PolicyEngine",
            "runtime_binding": "rexecop",
        },
        "stages": {
            "declared": status != "absent",
            "compiled": False,
            "bound_to_operation": False,
            "enforcement_projected": False,
        },
        "non_claims": [
            "RExecOp does not author or interpret policy semantics.",
            "RExecOp does not compute GovEngine policy reasoning.",
            "SCLite remains truth authority for emitted artifacts.",
        ],
    }
