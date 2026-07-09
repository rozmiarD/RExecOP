from __future__ import annotations

from pathlib import Path
from typing import Any

from rexecop.execution.govengine_governance import (
    EXPECTED_TYPED_EXECUTION_CONTROLS,
    evaluate_typed_execution_stack_compatibility,
)
from rexecop.profile.govengine_governance import evaluate_profile_governance

GOVERNANCE_CONTROLS_SCHEMA = "rexecop.governance_controls.v0.1"


def collect_governance_controls(
    *,
    profile: str | Path | None = None,
    track: str = "readonly",
) -> dict[str, Any]:
    """Project GovEngine typed-execution controls and optional profile governance."""
    stack = evaluate_typed_execution_stack_compatibility()
    catalog = stack["govengine_control_catalog"]
    controls = catalog.get("controls") if isinstance(catalog, dict) else []
    entries = catalog.get("entries") if isinstance(catalog, dict) else []
    control_ids = sorted(
        {
            str(item)
            for item in (controls or [])
            if isinstance(item, str) and item
        }
        | {
            str(item.get("control_id") or "")
            for item in (entries or [])
            if isinstance(item, dict) and item.get("control_id")
        }
    )
    profile_governance: dict[str, Any] | None = None
    if profile is not None:
        profile_governance = evaluate_profile_governance(profile, track=track)

    status = "passed" if stack["status"] == "passed" else "blocked"
    return {
        "schema": GOVERNANCE_CONTROLS_SCHEMA,
        "status": status,
        "required_controls": list(EXPECTED_TYPED_EXECUTION_CONTROLS),
        "control_catalog": catalog,
        "control_ids": control_ids,
        "typed_execution_stack": {
            "status": stack["status"],
            "supported_backends": stack["supported_backends"],
            "unsupported_backends": stack["unsupported_backends"],
            "missing_controls": stack["missing_controls"],
            "blockers": stack["blockers"],
        },
        "profile_governance": profile_governance,
        "non_claims": [
            "Does not evaluate GovEngine admission for a specific operation.",
            "Does not mutate policy packs, profiles, or runtime state.",
            "Control catalog is a GovEngine projection; RExecOp does not own policy.",
        ],
    }
