from __future__ import annotations

from pathlib import Path

import yaml

from rexecop.environment.model import Environment
from rexecop.errors import RExecOpValidationError


def load_environment(path: Path) -> Environment:
    if not path.is_file():
        raise RExecOpValidationError(f"environment file not found: {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise RExecOpValidationError(f"invalid environment yaml: {path}")
    try:
        return Environment.from_mapping(data)
    except ValueError as exc:
        raise RExecOpValidationError(str(exc)) from exc
