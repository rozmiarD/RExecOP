from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib

import pytest

from delivery_scope import DELIVERY_TEST_MODULES, SIGNOFF_SCRIPT_REL, repo_root

REPO_ROOT = repo_root()


def test_release_qualification_docs_present() -> None:
    assert (REPO_ROOT / "docs/release-qualification.md").is_file()
    assert (REPO_ROOT / "docs/release-qualification-record.md").is_file()
    assert (REPO_ROOT / SIGNOFF_SCRIPT_REL).is_file()


@pytest.mark.signoff_script
def test_alpha_signoff_script_matches_release_evidence_state() -> None:
    if os.environ.get("REXECOP_SIGNOFF_INNER") == "1":
        pytest.skip("sign-off script runs its own nested pytest suite")
    script = REPO_ROOT / SIGNOFF_SCRIPT_REL
    assert shutil.which("bash") is not None
    result = subprocess.run(
        ["bash", str(script)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHON": sys.executable},
    )
    review = subprocess.run(
        [sys.executable, "scripts/validate_external_review_gate.py"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    operational = subprocess.run(
        [sys.executable, "scripts/validate_m10_operational_gate.py"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        release_version = tomllib.load(handle)["project"]["version"]
    review_record = (
        REPO_ROOT / "docs" / "release-security-review" / f"{release_version}.json"
    )

    review_missing = not review_record.is_file()
    if review_missing:
        expected_review_error = f"review_record_missing:{review_record}"
        assert review.returncode == 1
        assert review.stdout == ""
        assert review.stderr.strip() == expected_review_error
    else:
        assert review.returncode == 0, review.stdout + review.stderr

    if operational.returncode != 0:
        expected_operational_error = "operational_qualification_version_drift"
        assert operational.returncode == 1
        assert operational.stdout == ""
        assert operational.stderr.strip() == expected_operational_error

    if review_missing:
        assert result.returncode != 0
        assert expected_review_error in output
    elif operational.returncode != 0:
        assert result.returncode != 0
        assert expected_operational_error in output
    else:
        assert result.returncode == 0, output


def test_delivery_scope_modules_exist() -> None:
    missing = [
        f"tests/{name}.py"
        for name in DELIVERY_TEST_MODULES
        if not (REPO_ROOT / "tests" / f"{name}.py").is_file()
    ]
    assert not missing, f"missing delivery scope modules: {missing}"
