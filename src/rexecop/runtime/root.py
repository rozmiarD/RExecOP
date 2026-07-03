from __future__ import annotations

import os
from pathlib import Path

from rexecop.errors import RExecOpValidationError

REXECOP_ROOT_ENV = "REXECOP_ROOT"
DEFAULT_RUNTIME_DIR = ".rexecop"


def resolve_runtime_root(explicit: str | Path | None = None) -> Path:
    raw = explicit if explicit is not None else os.environ.get(REXECOP_ROOT_ENV)
    if raw is None or str(raw).strip() == "":
        return (Path.cwd() / DEFAULT_RUNTIME_DIR).resolve()
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    resolved = path.resolve()
    if resolved.exists() and not resolved.is_dir():
        raise RExecOpValidationError(f"runtime root is not a directory: {resolved}")
    return resolved
