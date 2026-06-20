from __future__ import annotations

from rexecop.environment.model import Environment
from rexecop.environment.targets import describe_target


def target_criticality(environment: Environment, target: str) -> str:
    """Derive policy resource.criticality from environment target declarations."""
    info = describe_target(environment, target)
    if info.get("kind") == "unknown":
        return "medium"

    declared = str(info.get("declared_as") or info.get("name") or "").strip()
    if declared:
        spec = environment.targets.get(declared)
        if isinstance(spec, dict):
            explicit = str(spec.get("criticality") or spec.get("risk_class") or "").strip().lower()
            if explicit:
                return explicit

    group = str(info.get("group") or declared or info.get("name") or "").lower()
    name = str(info.get("name") or "").lower()
    if "critical" in group or "critical" in name or declared == "all_critical_vms":
        return "critical"
    return "low"
