#!/usr/bin/env python3
"""Offline release-train preflight for the GovEngine stack line.

Preflight runs before upload/publish and hard-fails on cross-repo pin drift,
validator constant mismatch, and docs compatibility drift. It reuses the
existing public-truth and stack-contract collectors instead of duplicating
their rules.

Public-index install smoke remains a separate online post-publish step.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from release_evidence import SCHEMA as CURRENT_EVIDENCE_SCHEMA  # noqa: E402
from release_evidence import load_record, validate_record  # noqa: E402

_STACK_REPO_ENV: dict[str, str] = {
    "govengine": "GOVSTACK_REPO_GOVENGINE",
    "sclite": "GOVSTACK_REPO_SCLITE",
    "tecrax": "GOVSTACK_REPO_TECRAX",
}
_DEFAULT_STACK_PARENT = ROOT.parent

_VALIDATOR_MODULES: tuple[Any, Any] | None = None

RELEASE_EVIDENCE_DIR = ROOT / "docs" / "release-evidence"
PREVIOUS_EVIDENCE_ENV = "REXECOP_PREVIOUS_RELEASE_EVIDENCE"


def stack_repos_from_env() -> dict[str, Path]:
    repos: dict[str, Path] = {}
    for name, env_var in _STACK_REPO_ENV.items():
        value = os.environ.get(env_var, "").strip()
        if value:
            repos[name] = Path(value)
            continue
        sibling = _DEFAULT_STACK_PARENT / name
        if (sibling / "pyproject.toml").is_file():
            repos[name] = sibling
    return repos


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable_to_load_module:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_toml_project(path: Path) -> dict[str, Any]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict):
        raise ValueError(f"{path}:missing_project_section")
    return project


def _dependency(project: dict[str, Any], name: str) -> str | None:
    for dependency in project.get("dependencies", []):
        text = str(dependency)
        if text.startswith(f"{name}==") or text.startswith(name):
            return text
    return None


def _optional_extra(project: dict[str, Any], extra: str) -> list[str]:
    optional = project.get("optional-dependencies") or {}
    items = optional.get(extra)
    if not isinstance(items, list):
        return []
    return [str(item) for item in items]


def _changelog_section(path: Path, version: str) -> str:
    text = path.read_text(encoding="utf-8")
    marker = f"## [{version}]"
    if marker not in text:
        return ""
    _, _, tail = text.partition(marker)
    next_heading = tail.find("\n## [")
    return tail if next_heading < 0 else tail[:next_heading]


def _evidence_errors(
    path: Path,
    *,
    expected_version: str,
    allow_supersedes: bool,
    require_current_schema: bool = False,
) -> list[str]:
    if not path.is_file():
        return [f"release_evidence_missing:{path}"]
    try:
        record = load_record(path)
    except (OSError, ValueError) as exc:
        return [f"release_evidence_unreadable:{path}:{exc}"]
    version = str(record.get("version") or "")
    supersedes = str(record.get("supersedes") or "")
    if version != expected_version and not (allow_supersedes and supersedes == expected_version):
        return [f"release_evidence_version_mismatch:{version}!={expected_version}"]
    errors = validate_record(record, expected_version=version)
    if require_current_schema and record.get("schema") != CURRENT_EVIDENCE_SCHEMA:
        errors.append(
            f"release_evidence_current_schema_required:{record.get('schema')}!="
            f"{CURRENT_EVIDENCE_SCHEMA}"
        )
    return errors


def _validator_modules() -> tuple[Any, Any]:
    global _VALIDATOR_MODULES
    if _VALIDATOR_MODULES is not None:
        return _VALIDATOR_MODULES
    public_truth = _load_module(
        "rexecop_validate_public_truth",
        ROOT / "scripts" / "validate_public_truth.py",
    )
    stack_contracts = _load_module(
        "rexecop_validate_stack_contracts",
        ROOT / "scripts" / "validate_stack_contracts.py",
    )
    _VALIDATOR_MODULES = (public_truth, stack_contracts)
    return _VALIDATOR_MODULES


def _assert_validator_constants_align(errors: list[str]) -> None:
    public_truth, stack_contracts = _validator_modules()
    pairs = (
        ("EXPECTED_GOVENGINE", public_truth.EXPECTED_GOVENGINE, stack_contracts.EXPECTED_GOVENGINE),
        ("EXPECTED_SCLITE", public_truth.EXPECTED_SCLITE, stack_contracts.EXPECTED_SCLITE),
        ("EXPECTED_TECRAX", public_truth.EXPECTED_TECRAX_EXTRA, stack_contracts.EXPECTED_TECRAX),
    )
    for label, left, right in pairs:
        if left != right:
            errors.append(f"validator_constant_mismatch:{label}:{left}!={right}")

    rexecop_version = public_truth.current_version()
    if rexecop_version != stack_contracts.EXPECTED_REXECOP:
        errors.append(
            "validator_constant_mismatch:rexecop_version:"
            f"{rexecop_version}!={stack_contracts.EXPECTED_REXECOP}"
        )


def _collect_sibling_repo_errors(
    errors: list[str],
    *,
    rexecop_version: str,
    expected_govengine: str,
    expected_sclite: str,
    expected_tecrax: str,
    stack_repos: dict[str, Path],
) -> None:
    for name in _STACK_REPO_ENV:
        repo = stack_repos.get(name)
        if repo is None:
            errors.append(f"sibling_repo_missing:{name}")
        elif not (repo / "pyproject.toml").is_file():
            errors.append(f"sibling_repo_invalid:{name}:{repo}")

    sclite_pin = expected_sclite.split("==", 1)[1]
    tecrax_pin = expected_tecrax.split("==", 1)[1]

    sclite_repo = stack_repos.get("sclite")
    if sclite_repo and (sclite_repo / "pyproject.toml").is_file():
        project = _read_toml_project(sclite_repo / "pyproject.toml")
        version = str(project.get("version", ""))
        if version != sclite_pin:
            errors.append(f"sclite_repo_version_mismatch:{version}!={sclite_pin}")

    govengine_repo = stack_repos.get("govengine")
    if govengine_repo and (govengine_repo / "pyproject.toml").is_file():
        project = _read_toml_project(govengine_repo / "pyproject.toml")
        dep = _dependency(project, "sclite-core")
        if dep != expected_sclite:
            errors.append(f"govengine_repo_sclite_pin_mismatch:{dep}!={expected_sclite}")

    tecrax_repo = stack_repos.get("tecrax")
    if tecrax_repo and (tecrax_repo / "pyproject.toml").is_file():
        project = _read_toml_project(tecrax_repo / "pyproject.toml")
        checks = (
            ("govengine", expected_govengine),
            ("sclite-core", expected_sclite),
            ("rexecop", f"rexecop=={rexecop_version}"),
        )
        for name, expected in checks:
            dep = _dependency(project, name)
            if dep != expected:
                errors.append(f"tecrax_repo_{name}_pin_mismatch:{dep}!={expected}")
        tecrax_version = str(project.get("version", ""))
        if tecrax_version != tecrax_pin:
            errors.append(f"tecrax_repo_version_mismatch:{tecrax_version}!={tecrax_pin}")


def collect_errors(
    *,
    post_publish: bool = False,
    current_evidence: Path | None = None,
    release_mode: bool = False,
    previous_evidence: Path | None = None,
    stack_repos: dict[str, Path] | None = None,
) -> list[str]:
    errors: list[str] = []
    public_truth, stack_contracts = _validator_modules()
    version = public_truth.current_version()

    _assert_validator_constants_align(errors)
    errors.extend(public_truth.collect_errors())
    errors.extend(stack_contracts.collect_errors())
    _collect_sibling_repo_errors(
        errors,
        rexecop_version=version,
        expected_govengine=public_truth.EXPECTED_GOVENGINE,
        expected_sclite=public_truth.EXPECTED_SCLITE,
        expected_tecrax=public_truth.EXPECTED_TECRAX_EXTRA,
        stack_repos=stack_repos if stack_repos is not None else stack_repos_from_env(),
    )

    if post_publish:
        errors.extend(
            _evidence_errors(
                current_evidence or RELEASE_EVIDENCE_DIR / f"{version}.json",
                expected_version=version,
                allow_supersedes=False,
                require_current_schema=True,
            )
        )
    if release_mode:
        expected_previous = public_truth.PUBLISHED_PYPI_VERSION
        evidence_path = previous_evidence
        if evidence_path is None:
            configured = os.environ.get(PREVIOUS_EVIDENCE_ENV, "").strip()
            evidence_path = Path(configured) if configured else None
        if evidence_path is None:
            errors.append(f"previous_release_evidence_not_configured:{expected_previous}")
        else:
            errors.extend(
                _evidence_errors(
                    evidence_path,
                    expected_version=expected_previous,
                    allow_supersedes=True,
                )
            )
    return errors


def success_line(
    version: str,
    *,
    post_publish: bool,
    govengine: str,
    sclite: str,
    tecrax: str,
) -> str:
    mode = "post_publish" if post_publish else "preflight"
    return (
        f"release_train_preflight_ok:{mode}:"
        f"rexecop=={version}:"
        f"{govengine}:{sclite}:{tecrax}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline GovEngine stack release-train preflight.")
    parser.add_argument(
        "--post-publish",
        action="store_true",
        help="Require public-index clean-install evidence marker for the current version.",
    )
    parser.add_argument("--current-evidence", type=Path, default=None)
    parser.add_argument(
        "--release",
        action="store_true",
        help="Require verified evidence for the previous supported public line.",
    )
    parser.add_argument("--previous-evidence", type=Path, default=None)
    args = parser.parse_args(argv)

    public_truth, _ = _validator_modules()
    version = public_truth.current_version()
    errors = collect_errors(
        post_publish=args.post_publish,
        current_evidence=args.current_evidence,
        release_mode=args.release,
        previous_evidence=args.previous_evidence,
    )
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(
        success_line(
            version,
            post_publish=args.post_publish,
            govengine=public_truth.EXPECTED_GOVENGINE,
            sclite=public_truth.EXPECTED_SCLITE,
            tecrax=public_truth.EXPECTED_TECRAX_EXTRA,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
