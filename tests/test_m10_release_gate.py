from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_m10_release_gate.py"


def _load():
    spec = importlib.util.spec_from_file_location("rexecop_m10_release_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _live_payloads(gate) -> dict[str, Any]:
    return {
        f"repos/{gate.REPOSITORY}/branches/main/protection": {
            "required_status_checks": {
                "strict": True,
                "contexts": sorted(gate.REQUIRED_MAIN_CHECKS),
            }
        },
        f"repos/{gate.REPOSITORY}/environments/pypi": {
            "protection_rules": [{"type": "branch_policy"}],
            "deployment_branch_policy": {
                "protected_branches": True,
                "custom_branch_policies": False,
            },
        },
        f"repos/{gate.REPOSITORY}/rulesets": [
            {"id": 123, "name": "Protect release tags"}
        ],
        f"repos/{gate.REPOSITORY}/rulesets/123": {
            "target": "tag",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": [gate.RELEASE_TAG_PATTERN]}},
            "rules": [{"type": "deletion"}, {"type": "update"}],
            "bypass_actors": [],
        },
    }


def test_live_release_protection_accepts_expected_state() -> None:
    gate = _load()
    payloads = _live_payloads(gate)

    assert gate.validate_live_release_protection(payloads.__getitem__) == []


def test_github_workflows_are_valid_yaml() -> None:
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), path


def test_live_release_protection_rejects_missing_tag_immutability() -> None:
    gate = _load()
    payloads = _live_payloads(gate)
    payloads[f"repos/{gate.REPOSITORY}/rulesets/123"]["rules"] = [{"type": "deletion"}]

    errors = gate.validate_live_release_protection(payloads.__getitem__)

    assert "github_release_tag_immutability_missing" in errors


def test_live_release_protection_rejects_unprotected_pypi_environment() -> None:
    gate = _load()
    payloads = _live_payloads(gate)
    payloads[f"repos/{gate.REPOSITORY}/environments/pypi"][
        "deployment_branch_policy"
    ] = None

    errors = gate.validate_live_release_protection(payloads.__getitem__)

    assert "github_pypi_protected_branches_missing" in errors
