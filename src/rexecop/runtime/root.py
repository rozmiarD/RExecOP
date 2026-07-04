from __future__ import annotations

import os
import re
from pathlib import Path

from rexecop.errors import RExecOpValidationError

REXECOP_ROOT_ENV = "REXECOP_ROOT"
REXECOP_INSTANCE_ENV = "REXECOP_INSTANCE"
DEFAULT_RUNTIME_DIR = ".rexecop"
INSTANCES_DIR = "instances"
INSTANCE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def resolve_runtime_root(
    explicit: str | Path | None = None,
    *,
    instance: str | None = None,
) -> Path:
    raw = explicit if explicit is not None else os.environ.get(REXECOP_ROOT_ENV)
    if raw is None or str(raw).strip() == "":
        selected_instance = resolve_runtime_instance(instance)
        if selected_instance:
            return (
                Path.cwd()
                / DEFAULT_RUNTIME_DIR
                / INSTANCES_DIR
                / selected_instance
            ).resolve()
        return (Path.cwd() / DEFAULT_RUNTIME_DIR).resolve()
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    resolved = path.resolve()
    if resolved.exists() and not resolved.is_dir():
        raise RExecOpValidationError(f"runtime root is not a directory: {resolved}")
    return resolved


def resolve_runtime_instance(explicit: str | None = None) -> str | None:
    raw = explicit if explicit is not None else os.environ.get(REXECOP_INSTANCE_ENV)
    if raw is None or str(raw).strip() == "":
        return None
    value = str(raw).strip()
    if not INSTANCE_TOKEN.fullmatch(value):
        raise RExecOpValidationError(
            "runtime instance must be a token matching [A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
        )
    return value
