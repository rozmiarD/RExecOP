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

EXPECTED_GOVENGINE = "govengine==0.16.8"
EXPECTED_SCLITE = "sclite-core==1.0.8"
EXPECTED_TECRAX_EXTRA = "tecrax==0.3.9a0"
PUBLISHED_PYPI_VERSION = "0.2.14a0"

VERSION_DOCS = (
    "README.md",
    "OPERATOR_RUNBOOK.md",
    "OPERATOR_LAB_RUNBOOK.md",
    "docs/known-limitations.md",
    "docs/distribution.md",
    "docs/stack-contract-compatibility.md",
)

STALE_OPERATOR_VERSIONS = (
    "0.1.1a0",
    "0.1.2a0",
    "0.1.3a0",
    "0.1.4a0",
    "0.1.4a1",
    "0.1.4a2",
    "0.1.5a0",
    "0.2.0a0",
    "0.2.1a0",
    "0.2.2a0",
    "0.2.3a0",
    "0.2.12a0",
    "0.2.13a0",
)

CLAIM_DOCS = (
    "README.md",
    "OPERATOR_RUNBOOK.md",
    "OPERATOR_LAB_RUNBOOK.md",
)

FORBIDDEN_CLAIMS = (
    "production-ready",
)

M3_M4_CLI_MARKERS = (
    "secrets doctor",
    "profiles list",
    "profile manifest",
    "profile harness",
    "connectors list",
    "capabilities list",
    "action list",
    "action show",
    "action preview",
    "action configure",
    "action diff",
    "action templates",
    "action policy-preview",
    "http.simple-get",
    "action validate",
    "secrets suggest-ref",
    "operations unavailable",
    "runtime recover",
    "backup create",
    "watchdog manual-record",
)

M3_M4_DOC_INDEX_MARKERS = (
    "docs/profile-developer-surface.md",
    "docs/secrets-operator.md",
    "docs/runtime-recovery-ops.md",
)

PYPI_DOC_MARKERS = (
    "img.shields.io/badge/package-rexecop%20",
    "https://pypi.org/project/rexecop/",
    'python -m pip install "rexecop==',
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _pyproject() -> dict:
    return tomllib.loads(_read("pyproject.toml"))["project"]


def _dependency(project: dict, name: str) -> str:
    prefix = name
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
            f"| Current source line | `{stale}`",
            f"package-rexecop%20{stale}",
        )
        for marker in markers:
            if marker in text:
                errors.append(f"{path}:stale_operator_version:{stale}:{marker}")


def _assert_pypi_docs(errors: list[str], version: str) -> None:
    readme = _read("README.md")
    for marker in PYPI_DOC_MARKERS:
        if marker not in readme:
            errors.append(f"README.md:missing_pypi_marker:{marker}")
    _require(
        errors,
        "README.md",
        f"https://pypi.org/project/rexecop/{PUBLISHED_PYPI_VERSION}/",
    )
    _require(
        errors,
        "README.md",
        f'python -m pip install "rexecop=={PUBLISHED_PYPI_VERSION}"',
    )
    if version != PUBLISHED_PYPI_VERSION:
        _require(errors, "README.md", f"Current source line | `{version}`")
        _require(errors, "README.md", "does not contain the watchdog decision truth path")
        _require(errors, "README.md", "manual recovery record path")
    _require(errors, "docs/distribution.md", "https://pypi.org/project/rexecop/")
    _require(errors, "docs/distribution.md", f"rexecop=={PUBLISHED_PYPI_VERSION}")


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

    badge = f"package-rexecop%20{PUBLISHED_PYPI_VERSION}"
    if badge not in _read("README.md"):
        errors.append(f"README.md:missing_badge:{badge}")

    _require(errors, "README.md", EXPECTED_GOVENGINE)
    _require(errors, "README.md", EXPECTED_SCLITE)
    _require(errors, "OPERATOR_RUNBOOK.md", "scripts/validate_public_truth.py")
    _require(errors, "OPERATOR_LAB_RUNBOOK.md", "scripts/validate_public_truth.py")
    _require(errors, "README.md", "docs/stack-contract-compatibility.md")
    _require(errors, "docs/known-limitations.md", "Stack readiness labels")
    _require(errors, "docs/stack-contract-compatibility.md", "stack-contract-compatibility")
    _require(errors, "README.md", "contracts cli")
    _require(errors, "README.md", "rexecop.cli_error.v0.1")
    _require(errors, "docs/cli-reference.md", "rexecop.cli_contract_registry.v0.1")
    _require(errors, "docs/cli-reference.md", "rexecop.cli_error.v0.1")
    _require(errors, "docs/stack-contract-compatibility.md", EXPECTED_SCLITE)
    _require(errors, "docs/stack-contract-compatibility.md", EXPECTED_GOVENGINE)
    _require(errors, "docs/stack-contract-compatibility.md", EXPECTED_TECRAX_EXTRA)
    _require(errors, "docs/stack-contract-compatibility.md", "`mutation_ready` | false")
    _require(errors, "docs/architecture.md", EXPECTED_GOVENGINE)
    _require(errors, "docs/architecture.md", "examples/first-run-demo")
    _require(errors, "docs/architecture.md", "runtime/")
    _require(errors, "README.md", "docs/first-run.md")
    _require(errors, "README.md", "validate_first_run_smoke.py")
    _require(errors, "README.md", "operation review")
    _require(errors, "README.md", "operation diff")
    _require(errors, "README.md", "receipt show")
    _require(errors, "README.md", "evidence show")
    _require(errors, "README.md", "chain summary")
    _require(errors, "README.md", "support bundle --redacted")
    _require(errors, "README.md", "runtime status")
    _require(errors, "README.md", "explain-error")
    _require(errors, "README.md", "dead-letter list")
    _require(errors, "README.md", "locks list")
    _require(errors, "README.md", "runbook show")
    for marker in M3_M4_CLI_MARKERS:
        _require(errors, "README.md", marker)
    for marker in M3_M4_DOC_INDEX_MARKERS:
        _require(errors, "README.md", marker)
    _require(errors, "docs/govengine-integration.md", "govengine-supervisor explain")
    _require(errors, "docs/govengine-integration.md", "explain_supervisor_action()")
    _require(errors, "docs/profile-developer-surface.md", "govengine_governance")
    _require(errors, "docs/profile-developer-surface.md", "operator_metadata.yaml")
    _require(errors, "docs/profile-developer-surface.md", "rexecop.operation_profile_explain.v0.1")
    _require(errors, "docs/profile-developer-surface.md", "rexecop.profile_workflow_harness.v0.1")
    _require(errors, "docs/profile-developer-surface.md", "rexecop.action_list.v0.1")
    _require(errors, "docs/profile-developer-surface.md", "rexecop.action_show.v0.1")
    _require(errors, "docs/profile-developer-surface.md", "rexecop.action_preview.v0.1")
    _require(errors, "docs/profile-developer-surface.md", "rexecop.action_configure.v0.1")
    _require(errors, "docs/profile-developer-surface.md", "rexecop.action_diff.v0.1")
    _require(errors, "docs/profile-developer-surface.md", "http.simple-get")
    _require(errors, "docs/profile-developer-surface.md", "shell.readonly-allowlist")
    _require(errors, "docs/profile-developer-surface.md", "rexecop.action_validate.v0.1")
    _require(errors, "docs/secrets-operator.md", "rexecop.secrets_suggest_ref.v0.1")
    _require(errors, "docs/profile-developer-surface.md", "profile harness")
    _require(errors, "CHANGELOG.md", "rexecop.profile_workflow_harness.v0.1")
    _require(errors, "CHANGELOG.md", "profile harness")
    _require(errors, "CHANGELOG.md", "rexecop action list")
    _require(errors, "CHANGELOG.md", "rexecop action show")
    _require(errors, "CHANGELOG.md", "rexecop action preview")
    _require(errors, "CHANGELOG.md", "rexecop action configure")
    _require(errors, "CHANGELOG.md", "rexecop action diff")
    _require(errors, "CHANGELOG.md", "rexecop action validate")
    _require(errors, "CHANGELOG.md", "rexecop secrets suggest-ref")
    _require(errors, "docs/govengine-integration.md", "profile-governance")
    _require(errors, "CHANGELOG.md", "operator_metadata.yaml")
    _require(errors, "CHANGELOG.md", "rexecop.operation_profile_explain.v0.1")
    _require(errors, "OPERATOR_RUNBOOK.md", "secrets doctor")
    _require(errors, "OPERATOR_RUNBOOK.md", "operations unavailable")
    _require(errors, "OPERATOR_RUNBOOK.md", "runtime recover")
    for marker in M3_M4_DOC_INDEX_MARKERS:
        _require(errors, "OPERATOR_RUNBOOK.md", marker)
    _require(errors, "CHANGELOG.md", "secrets doctor")
    _require(errors, "CHANGELOG.md", "runtime recover")
    _require(errors, "CHANGELOG.md", "operations unavailable")
    _require(errors, "docs/alpha-sign-off.md", "validate_first_run_smoke.py")
    _require(errors, "docs/alpha-sign-off.md", "validate_stack_contracts.py")
    _require(errors, ".github/workflows/ci.yml", "validate_first_run_smoke.py")
    _require(errors, "scripts/validate_stack_contracts.py", "stack_contracts_ok")
    _require(errors, "scripts/validate_profile_conformance.py", "profile_conformance_ok")
    _require(errors, "scripts/validate_first_run_smoke.py", "first_run_smoke_ok")
    _require(errors, "docs/first-run.md", "rexecop --root /tmp/rexecop-first-run init --guided")
    _require(errors, "README.md", "docs/first-run.md")
    _require(errors, ".github/workflows/ci.yml", "python scripts/validate_public_truth.py")
    _require(errors, ".github/workflows/ci.yml", "python scripts/validate_stack_contracts.py")
    _require(errors, ".github/workflows/ci.yml", "python scripts/validate_profile_conformance.py")
    _require(errors, ".github/workflows/ci.yml", "rozmiarD/tecrax")
    _require(errors, ".github/workflows/ci.yml", "python -m build")
    _require(errors, ".github/workflows/ci.yml", "twine check")
    _require(errors, ".github/workflows/ci.yml", "validate_distribution.py")
    _require(errors, ".github/workflows/publish.yml", "workflow_dispatch")
    _require(errors, ".github/workflows/publish.yml", "twine upload")
    _require(errors, ".github/workflows/publish.yml", "validate_distribution.py")

    init_text = _read("src/rexecop/__init__.py")
    if f'__version__ = "{version}"' not in init_text:
        errors.append("src/rexecop/__init__.py:missing_version_literal")

    _assert_pypi_docs(errors, version)

    for path in CLAIM_DOCS:
        lowered = _read(path).lower()
        for claim in FORBIDDEN_CLAIMS:
            claim_text = claim.lower()
            if claim_text in lowered and f"not {claim_text}" not in lowered:
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
