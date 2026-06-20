from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from delivery_scope import DELIVERY_TEST_MODULES, SIGNOFF_SCRIPT_REL, repo_root

REPO_ROOT = repo_root()


def test_alpha_signoff_docs_present() -> None:
    assert (REPO_ROOT / "docs/alpha-sign-off.md").is_file()
    assert (REPO_ROOT / "docs/alpha-sign-off-record.md").is_file()
    assert (REPO_ROOT / SIGNOFF_SCRIPT_REL).is_file()


@pytest.mark.signoff_script
def test_alpha_signoff_script_passes() -> None:
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
    assert result.returncode == 0, result.stdout + result.stderr


def test_delivery_scope_modules_exist() -> None:
    missing = [
        f"tests/{name}.py"
        for name in DELIVERY_TEST_MODULES
        if not (REPO_ROOT / "tests" / f"{name}.py").is_file()
    ]
    assert not missing, f"missing delivery scope modules: {missing}"
