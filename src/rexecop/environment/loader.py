from __future__ import annotations

from pathlib import Path

import yaml

from rexecop.environment.model import Environment
from rexecop.errors import RExecOpValidationError


def load_environment(path: Path) -> Environment:
    return _load_environment(path, inspect_secret_ref_collisions=False)


def _load_environment_for_secret_inspection(path: Path) -> Environment:
    return _load_environment(path, inspect_secret_ref_collisions=True)


def _load_environment(
    path: Path,
    *,
    inspect_secret_ref_collisions: bool,
) -> Environment:
    if not path.is_file():
        raise RExecOpValidationError(f"environment file not found: {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise RExecOpValidationError(f"invalid environment yaml: {path}")
    try:
        environment = Environment.from_mapping(data)
    except ValueError as exc:
        raise RExecOpValidationError(str(exc)) from exc
    if not inspect_secret_ref_collisions:
        from rexecop.secrets.reference import enforce_secret_ref_env_collision_freedom

        enforce_secret_ref_env_collision_freedom(environment.as_dict())
    return environment
