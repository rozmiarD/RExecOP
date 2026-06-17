from __future__ import annotations

from pathlib import Path

import pytest

from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.profile.loader import load_profile
from rexecop.profile.resolver import list_registered_profiles, resolve_profile_path
from rexecop.storage.file_store import FileStore
from rexecop.validation.validator import validate_operation_result

REPO_ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"
FIXTURE_PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"

tecrax = pytest.importorskip("tecrax")


def test_tecrax_profile_entry_point_registered() -> None:
    assert "tecrax" in list_registered_profiles()
    resolved = resolve_profile_path("tecrax")
    profile = load_profile(resolved)
    assert profile.name == "tecrax"
    assert profile.version == "0.3.1"


def test_core_has_no_tecrax_profile_imports() -> None:
    src_root = REPO_ROOT / "src" / "rexecop"
    offenders: list[str] = []
    for path in src_root.rglob("*.py"):
        text = path.read_text()
        if "tecrax_profile" in text or "import tecrax" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_tecrax_profile_check_backup_status_e2e(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path="tecrax",
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )
    assert operation.state == OperationState.PLANNED.value
    assert operation.profile == "tecrax"

    completed = controller.start(operation.id)
    assert completed.state == OperationState.COMPLETED.value

    validation = controller.validate(operation.id)
    assert validation["passed"] is True


def test_validator_requires_profile_root_for_unknown_intent() -> None:
    profile = load_profile(FIXTURE_PROFILE)
    try:
        validate_operation_result(
            intent="unknown_intent",
            shared_state={},
            profile=profile,
        )
    except Exception as exc:
        assert "no validation rules" in str(exc)
    else:
        raise AssertionError("expected validation error")
