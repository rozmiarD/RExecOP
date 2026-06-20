from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import rexecop
from delivery_scope import DELIVERY_TEST_MODULES

REPO_ROOT = Path(__file__).resolve().parents[1]


def _package_version() -> str:
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def test_alpha_version_declared() -> None:
    assert rexecop.__version__ == _package_version()


def test_delivery_e2e_modules_registered() -> None:
    required = {
        "test_readonly_vertical_slice_e2e",
        "test_staging_connectors_e2e",
        "test_http_health_check_e2e",
        "test_apply_vertical_slice_e2e",
    }
    missing = required - set(DELIVERY_TEST_MODULES)
    assert not missing, f"delivery scope missing E2E modules: {sorted(missing)}"


def test_operator_lab_runbook_present() -> None:
    assert (REPO_ROOT / "OPERATOR_LAB_RUNBOOK.md").is_file()
    assert (REPO_ROOT / "OPERATOR_RUNBOOK.md").is_file()
    assert (REPO_ROOT / "CHANGELOG.md").is_file()
    assert (REPO_ROOT / "docs/known-limitations.md").is_file()
    assert (REPO_ROOT / "docs/architecture.md").is_file()


def test_secret_scan_script_passes() -> None:
    script = REPO_ROOT / "scripts/secret_scan.sh"
    assert script.is_file()
    assert shutil.which("bash") is not None
    result = subprocess.run(["bash", str(script)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_rexecop_cli_entrypoint() -> None:
    rexecop_bin = shutil.which("rexecop")
    if rexecop_bin is None:
        pytest.skip("rexecop console script not on PATH")
    result = subprocess.run([rexecop_bin, "version"], check=False, capture_output=True, text=True)
    assert result.returncode == 0
    assert _package_version() in result.stdout
