from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from rexecop.errors import RExecOpValidationError


def load_validation_rule_spec(profile_root: Path, intent: str) -> dict[str, Any]:
    path = profile_root / "validation_rules" / f"{intent}.yaml"
    if not path.is_file():
        raise RExecOpValidationError(f"no validation rules for intent: {intent}")

    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise RExecOpValidationError(f"invalid validation rules file: {path}")

    spec = data.get("validation_rule")
    if not isinstance(spec, dict):
        raise RExecOpValidationError(f"validation_rule mapping missing in: {path}")

    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RExecOpValidationError(f"validation_rule.steps required in: {path}")

    return spec
