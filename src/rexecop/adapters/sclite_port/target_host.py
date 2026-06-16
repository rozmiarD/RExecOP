from __future__ import annotations

import re

from sclite.hosts import extract_host

from rexecop.operation.plan import OperationPlan

_HOST_TOKEN_RE = re.compile(r"[^a-z0-9.-]+")


def resolve_sclite_target_host(plan: OperationPlan) -> str:
    """Resolve an explicit DNS-style host for SCLite scope-fidelity review.

    Logical RExecOp targets (for example ``all_critical_vms``) are mapped to a
    stable fixture hostname derived from the environment id when no host can be
    extracted from the target label itself.
    """
    for candidate in (plan.target, f"host:{plan.target}"):
        host = extract_host(candidate)
        if host:
            return host
    env_token = _HOST_TOKEN_RE.sub("-", plan.environment.strip().lower()).strip("-")
    if not env_token:
        env_token = "rexecop"
    if "." not in env_token:
        return f"{env_token}.fixture"
    return env_token


def sclite_target_ref(target_host: str) -> str:
    return f"host:{target_host}"
