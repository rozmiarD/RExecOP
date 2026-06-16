from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from rexecop.errors import RExecOpValidationError
from rexecop.profile.contract import validate_profile_contract


@dataclass
class LoadedProfile:
    root: Path
    contract: dict[str, Any]
    name: str
    version: str

    def intent_path(self, intent_id: str) -> Path:
        path = self.root / "intents" / f"{intent_id}.yaml"
        if not path.is_file():
            raise RExecOpValidationError(f"intent not found: {intent_id}")
        return path

    def resolve_workflow_path(self, intent_id: str) -> Path:
        intent_data = yaml.safe_load(self.intent_path(intent_id).read_text())
        if not isinstance(intent_data, dict):
            raise RExecOpValidationError(f"invalid intent file: {intent_id}")
        intent = intent_data.get("intent")
        if not isinstance(intent, dict):
            raise RExecOpValidationError(f"intent mapping missing for: {intent_id}")
        workflow_ref = str(intent.get("workflow") or "").strip()
        if not workflow_ref:
            raise RExecOpValidationError(f"intent.workflow missing for: {intent_id}")
        workflow_path = (self.root / workflow_ref).resolve()
        if not workflow_path.is_file():
            raise RExecOpValidationError(
                f"workflow not found for intent {intent_id}: {workflow_ref}"
            )
        root = self.root.resolve()
        if root not in workflow_path.parents and workflow_path != root:
            raise RExecOpValidationError(f"workflow escapes profile root: {workflow_ref}")
        return workflow_path

    def intent_metadata(self, intent_id: str) -> dict[str, Any]:
        intent_data = yaml.safe_load(self.intent_path(intent_id).read_text())
        if not isinstance(intent_data, dict):
            raise RExecOpValidationError(f"invalid intent file: {intent_id}")
        intent = intent_data.get("intent")
        if not isinstance(intent, dict):
            raise RExecOpValidationError(f"intent mapping missing for: {intent_id}")
        return dict(intent)


def load_profile(profile_path: Path) -> LoadedProfile:
    if profile_path.is_dir():
        profile_file = profile_path / "profile.yaml"
    else:
        profile_file = profile_path
        profile_path = profile_path.parent

    if not profile_file.is_file():
        raise RExecOpValidationError(f"profile file not found: {profile_file}")

    data = yaml.safe_load(profile_file.read_text())
    if not isinstance(data, dict):
        raise RExecOpValidationError(f"invalid profile yaml: {profile_file}")

    contract = validate_profile_contract(data)
    return LoadedProfile(
        root=profile_path,
        contract=contract,
        name=str(contract["name"]),
        version=str(contract["version"]),
    )
