from __future__ import annotations

from pathlib import Path
from typing import Any

from rexecop.errors import RExecOpValidationError
from rexecop.yaml_input import load_yaml_file


def load_validation_rule_spec(profile_root: Path, intent: str) -> dict[str, Any]:
    path = profile_root / "validation_rules" / f"{intent}.yaml"
    if not path.is_file():
        raise RExecOpValidationError(f"no validation rules for intent: {intent}")

    data = load_yaml_file(path)
    if not isinstance(data, dict):
        raise RExecOpValidationError(f"invalid validation rules file: {path}")

    spec = data.get("validation_rule")
    if not isinstance(spec, dict):
        raise RExecOpValidationError(f"validation_rule mapping missing in: {path}")

    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RExecOpValidationError(f"validation_rule.steps required in: {path}")

    return spec
