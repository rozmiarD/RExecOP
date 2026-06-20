from __future__ import annotations

import re

import pytest

from delivery_scope import (
    DELIVERY_TEST_MODULES,
    DELIVERY_THEMES,
    SIGNOFF_BUILD_ENV,
    SIGNOFF_GATE_MODULE,
    SIGNOFF_INNER_ENV,
    SIGNOFF_PYTEST_MARKER,
    SIGNOFF_SCRIPT_REL,
    delivery_module_paths,
    repo_root,
)

REPO_ROOT = repo_root()


def test_delivery_themes_map_to_delivery_modules() -> None:
    missing = [
        f"{theme}:{module}"
        for theme, module in DELIVERY_THEMES.items()
        if module not in DELIVERY_TEST_MODULES
    ]
    assert not missing, f"theme maps to module outside delivery scope: {missing}"


def test_delivery_module_files_exist() -> None:
    missing = [
        str(path.relative_to(REPO_ROOT))
        for path in delivery_module_paths()
        if not path.is_file()
    ]
    assert not missing, f"missing delivery modules: {missing}"


def test_signoff_gate_module_not_in_delivery_scope() -> None:
    assert SIGNOFF_GATE_MODULE not in DELIVERY_TEST_MODULES


def test_signoff_script_uses_delivery_marker() -> None:
    script = (REPO_ROOT / SIGNOFF_SCRIPT_REL).read_text(encoding="utf-8")
    assert f"-m {SIGNOFF_PYTEST_MARKER}" in script
    assert f'export {SIGNOFF_INNER_ENV}=1' in script
    assert SIGNOFF_GATE_MODULE not in script
    assert "test_alpha_signoff_gate.py" not in script


def test_signoff_script_build_is_opt_in() -> None:
    script = (REPO_ROOT / SIGNOFF_SCRIPT_REL).read_text(encoding="utf-8")
    assert SIGNOFF_BUILD_ENV in script
    assert "python -m build" not in script or SIGNOFF_BUILD_ENV in script


@pytest.mark.parametrize("module_name", DELIVERY_TEST_MODULES)
def test_delivery_modules_are_canonical(module_name: str) -> None:
    assert (REPO_ROOT / "tests" / f"{module_name}.py").is_file()


def test_no_delivery_module_invokes_signoff_script() -> None:
  pattern = re.compile(r"run_alpha_signoff_checks\.sh")
  offenders: list[str] = []
  for path in delivery_module_paths():
      if pattern.search(path.read_text(encoding="utf-8")):
          offenders.append(str(path.relative_to(REPO_ROOT)))
  assert offenders == [], f"delivery modules must not shell out to sign-off script: {offenders}"
