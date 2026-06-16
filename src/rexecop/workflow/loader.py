from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from rexecop.errors import RExecOpValidationError
from rexecop.workflow.model import Workflow


def load_workflow(path: Path) -> Workflow:
    if not path.is_file():
        raise RExecOpValidationError(f"workflow file not found: {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise RExecOpValidationError(f"invalid workflow yaml: {path}")
    try:
        return Workflow.from_mapping(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise RExecOpValidationError(f"invalid workflow content: {path}") from exc


def workflow_dict_from_file(path: Path) -> dict[str, Any]:
    workflow = load_workflow(path)
    return workflow.as_dict()
