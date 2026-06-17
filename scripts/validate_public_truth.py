#!/usr/bin/env python3
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import rexecop  # noqa: E402

EXPECTED_GOVENGINE = "govengine>=0.12.2a0,<0.15"
EXPECTED_SCLITE = "sclite-core>=1.0.1,<1.1"
EXPECTED_TECRAX_EXTRA = "tecrax>=0.3.1a0,<0.4"

VERSION_DOCS = (
    "README.md",
    "OPERATOR_RUNBOOK.md",
    "OPERATOR_LAB_RUNBOOK.md",
    "docs/known-limitations.md",
)

STALE_OPERATOR_VERSIONS = (
    "0.1.1a0",
    "0.1.2a0",
    "0.1.3a0",
)

CLAIM_DOCS = (
    "README.md",
    "OPERATOR_RUNBOOK.md",
    "OPERATOR_LAB_RUNBOOK.md",
)

FORBIDDEN_CLAIMS = (
    "production-ready",
    "pypi published",
    "published on pypi",
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _pyproject() -> dict:
    return tomllib.loads(_read("pyproject.toml"))["project"]


def _dependency(project: dict, name: str) -> str:
    prefix = f"{name}>="
    for dependency in project.get("dependencies", []):
        text = str(dependency)
        if text.startswith(prefix):
            return text
    raise AssertionError(f"missing_dependency:{name}")


def _optional_extra(project: dict, extra: str) -> list[str]:
    optional = project.get("optional-dependencies") or {}
    items = optional.get(extra)
    if not isinstance(items, list):
        raise AssertionError(f"missing_optional_extra:{extra}")
    return [str(item) for item in items]


def _require(errors: list[str], path: str, expected: str) -> None:
    if expected not in _read(path):
        errors.append(f"{path}:missing:{expected}")


def _reject_stale_operator_versions(errors: list[str], path: str, text: str, current: str) -> None:
    for stale in STALE_OPERATOR_VERSIONS:
        if stale == current:
            continue
        markers = (
            f"expect {stale}",
            f"rexecop {stale}",
            f"RExecOp `{stale}`",
            f"RExecOp **alpha** (`{stale}`)",
            f"| Version | `{stale}` |",
            f"package-rexecop%20{stale}",
        )
        for marker in markers:
            if marker in text:
                errors.append(f"{path}:stale_operator_version:{stale}:{marker}")


def current_version() -> str:
    return str(_pyproject()["version"])


def collect_errors() -> list[str]:
    errors: list[str] = []
    project = _pyproject()
    version = str(project["version"])
    govengine_dep = _dependency(project, "govengine")
    sclite_dep = _dependency(project, "sclite-core")
    tecrax_extra = _optional_extra(project, "tecrax")

    if project["name"] != "rexecop":
        errors.append(f'distribution_name_mismatch:{project["name"]}')
    if rexecop.__version__ != version:
        errors.append(f"package_version_mismatch:{rexecop.__version__}!={version}")
    if govengine_dep != EXPECTED_GOVENGINE:
        errors.append(f"govengine_dependency_mismatch:{govengine_dep}!={EXPECTED_GOVENGINE}")
    if sclite_dep != EXPECTED_SCLITE:
        errors.append(f"sclite_dependency_mismatch:{sclite_dep}!={EXPECTED_SCLITE}")
    if EXPECTED_TECRAX_EXTRA not in tecrax_extra:
        errors.append(f"tecrax_extra_mismatch:{tecrax_extra}")

    changelog = _read("CHANGELOG.md")
    if f"## [{version}]" not in changelog:
        errors.append(f"CHANGELOG.md:missing_release_section:[{version}]")

    for path in VERSION_DOCS:
        text = _read(path)
        _require(errors, path, version)
        _reject_stale_operator_versions(errors, path, text, version)

    badge = f"package-rexecop%20{version}"
    if badge not in _read("README.md"):
        errors.append(f"README.md:missing_badge:{badge}")

    _require(errors, "README.md", EXPECTED_GOVENGINE)
    _require(errors, "README.md", EXPECTED_SCLITE)
    _require(errors, "OPERATOR_RUNBOOK.md", "scripts/validate_public_truth.py")
    _require(errors, "OPERATOR_LAB_RUNBOOK.md", "scripts/validate_public_truth.py")
    _require(errors, ".github/workflows/ci.yml", "python scripts/validate_public_truth.py")
    _require(errors, ".github/workflows/ci.yml", "rozmiarD/tecrax")

    init_text = _read("src/rexecop/__init__.py")
    if f'__version__ = "{version}"' not in init_text:
        errors.append("src/rexecop/__init__.py:missing_version_literal")

    for path in CLAIM_DOCS:
        lowered = _read(path).lower()
        for claim in FORBIDDEN_CLAIMS:
            claim_text = claim.lower()
            if claim_text in lowered and f"not {claim_text}" not in lowered:
                if claim == "pypi published" and "not published" in lowered:
                    continue
                if claim == "published on pypi" and "not published" in lowered:
                    continue
                errors.append(f"{path}:forbidden_claim:{claim}")

    return errors


def main() -> int:
    version = current_version()
    errors = collect_errors()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(
        f"public_truth_ok:rexecop=={version}:"
        f"{EXPECTED_GOVENGINE}:{EXPECTED_SCLITE}:{EXPECTED_TECRAX_EXTRA}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
