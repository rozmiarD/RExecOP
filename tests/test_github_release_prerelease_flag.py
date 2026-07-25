from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "github_release_prerelease_flag.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location(
        "rexecop_github_release_prerelease_flag",
        SCRIPT,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "version",
    ("1.0.0a1", "1.0.0b2", "1.0.0rc1", "1.0.0.dev3", "1!2.0rc4"),
)
def test_prerelease_versions_emit_github_flag(version: str) -> None:
    assert _load_helper().github_release_prerelease_flag(version) == "--prerelease"


@pytest.mark.parametrize("version", ("1.0.0", "1.0.0.post1", "2!1.0"))
def test_final_versions_do_not_emit_github_flag(version: str) -> None:
    assert _load_helper().github_release_prerelease_flag(version) == ""


def test_invalid_version_fails_closed() -> None:
    with pytest.raises(ValueError, match=r"^invalid_pep440_version:not a version$"):
        _load_helper().github_release_prerelease_flag("not a version")
