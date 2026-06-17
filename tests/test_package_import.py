from __future__ import annotations

import tomllib
from pathlib import Path

import rexecop

ROOT = Path(__file__).resolve().parents[1]


def package_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def test_package_import() -> None:
    assert rexecop.__version__


def test_version_matches_pyproject() -> None:
    assert rexecop.__version__ == package_version()
