from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import rexecop

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_alpha_version_declared() -> None:
    assert rexecop.__version__ == "0.1.3a0"


def test_readonly_fixture_e2e_exists() -> None:
    """Alpha exit: read_only E2E on fixture profile."""
    path = REPO_ROOT / "tests/test_readonly_vertical_slice_e2e.py"
    assert path.is_file()
    text = path.read_text()
    assert "check_backup_status" in text
    assert "COMPLETED" in text


def test_staging_http_readonly_e2e_exists() -> None:
    """Alpha exit: read_only path via http_api staging connector."""
    path = REPO_ROOT / "tests/test_staging_connectors_e2e.py"
    assert path.is_file()
    text = path.read_text()
    assert "test_readonly_check_backup_status_against_staging_http" in text


def test_http_health_golden_path_exists() -> None:
    path = REPO_ROOT / "tests/test_http_health_check_e2e.py"
    assert path.is_file()
    assert "http_health_check" in path.read_text()


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
    assert "0.1.3a0" in result.stdout
