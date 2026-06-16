from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rexecop.errors import RExecOpValidationError
from rexecop.profile.contract import validate_profile_contract
from rexecop.profile.loader import load_profile

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"


def test_valid_profile_contract_loads() -> None:
    profile = load_profile(PROFILE)
    assert profile.name == "tecrax"
    assert profile.version == "0.1.0"


def test_invalid_profile_contract_fails(tmp_path: Path) -> None:
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump({"profile_contract": {"name": "x", "version": "0.1.0"}}))
    with pytest.raises(RExecOpValidationError):
        validate_profile_contract(yaml.safe_load(path.read_text()))
