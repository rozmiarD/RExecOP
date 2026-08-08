#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import rexecop  # noqa: E402
from rexecop.public_api import public_api_manifest  # noqa: E402

EXPECTED_GOVENGINE = "govengine==1.0.0rc2"
EXPECTED_SCLITE = "sclite-core==2.0.1"
EXPECTED_TECRAX_CONSUMER = "0.4.0rc3"
EXPECTED_GOVENGINE_STATUS = "`1.0.0rc1` (public release candidate)"
PUBLISHED_PYPI_VERSION = "1.0.0rc1"
PUBLISHED_GOVENGINE = "govengine==1.0.0rc1"
PUBLISHED_SCLITE = "sclite-core==2.0.0"
SCLITE_SOURCE_REF = "c065d7a157665351054bacc7b5e3ae12b7cc9d98"
GOVENGINE_SOURCE_REF = "e65ad22ec25d74bbbb4969bd614981a8ed5e47c8"

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
    "0.2.14a0",
)

M8_CLAIM_MARKERS = (
    "rexecop.cli_contract_registry.v0.1",
    "rexecop.cli_error.v0.1",
    "rexecop.structured_log_event.v0.1",
    "rexecop.runtime_diagnostics.v0.1",
    "observability logs list",
    "contracts cli",
)

CLAIM_DOCS = (
    "README.md",
    "OPERATOR_RUNBOOK.md",
    "OPERATOR_LAB_RUNBOOK.md",
)

FORBIDDEN_CLAIMS = ("production-ready",)

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


def _project_markdown_paths(*, include_archive: bool) -> list[Path]:
    candidates = [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]
    return sorted(
        path
        for path in candidates
        if path.is_file() and (include_archive or "archive" not in path.relative_to(ROOT).parts)
    )


def _current_markdown_paths() -> list[Path]:
    return _project_markdown_paths(include_archive=False)


def _all_markdown_paths() -> list[Path]:
    return _project_markdown_paths(include_archive=True)


def _github_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if not re.match(r"^#{1,6}\s+", line):
            continue
        heading = re.sub(r"^#{1,6}\s+", "", line).strip().lower()
        heading = re.sub(r"[`*_~]", "", heading)
        heading = re.sub(r"[^\w\s-]", "", heading, flags=re.UNICODE)
        slug = re.sub(r"[\s-]+", "-", heading).strip("-")
        if not slug:
            continue
        occurrence = counts.get(slug, 0)
        counts[slug] = occurrence + 1
        anchors.add(slug if occurrence == 0 else f"{slug}-{occurrence}")
    return anchors


def _validate_markdown_links(errors: list[str]) -> None:
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    for path in _all_markdown_paths():
        text = path.read_text(encoding="utf-8")
        for raw_target in link_pattern.findall(text):
            target = raw_target.strip().strip("<>")
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            file_part, _, anchor = target.partition("#")
            target_path = path if not file_part else (path.parent / file_part).resolve()
            try:
                target_path.relative_to(ROOT)
            except ValueError:
                errors.append(f"{path.relative_to(ROOT)}:markdown_link_outside_repo:{target}")
                continue
            if not target_path.is_file():
                errors.append(f"{path.relative_to(ROOT)}:markdown_link_missing:{target}")
                continue
            if anchor and anchor not in _github_anchors(target_path.read_text(encoding="utf-8")):
                errors.append(f"{path.relative_to(ROOT)}:markdown_anchor_missing:{target}")


def _validate_document_semantics(errors: list[str]) -> None:
    changelog = _read("CHANGELOG.md")
    if changelog.count("## Unreleased") != 1:
        errors.append("CHANGELOG.md:unreleased_heading_must_be_unique")
    for forbidden in (
        "current PyPI alpha line is",
        "## Pre-alpha limits",
        "RExecOp is **alpha** software",
        "Alpha limitations accepted",
        "SCLite owns the chain artifact shape",
        "SCLite observation envelope",
        "SCLite `automation_chain",
        "SCLite trigger decision artifact",
        "SCLite watchdog decision artifact",
        "runs profile-defined operations under GovEngine admission",
    ):
        for path in _current_markdown_paths():
            if forbidden in path.read_text(encoding="utf-8"):
                errors.append(f"{path.relative_to(ROOT)}:stale_semantic_claim:{forbidden}")
    if (ROOT / "docs" / "evidence-model.md").exists():
        errors.append("docs/evidence-model.md:duplicate_surface_must_be_merged")
    readme = _read("README.md")
    for heading in ("## What RExecOp claims", "## What RExecOp does not claim"):
        if heading not in readme:
            errors.append(f"README.md:missing_claim_boundary:{heading}")
    for marker in ("legacy_read_only", "stable_read_only"):
        if marker not in readme:
            errors.append(f"README.md:missing_runtime_boundary:{marker}")
    evidence_docs = _read("docs/release-evidence/README.md")
    if re.search(r"installed .*Tecrax", evidence_docs, flags=re.IGNORECASE):
        errors.append("docs/release-evidence/README.md:tecrax_not_release_inventory")

    cli_reference = _read("docs/cli-reference.md")
    documented_cli_rows = {
        match.group(1).strip()
        for line in cli_reference.splitlines()
        if (match := re.match(r"^\|\s*`([^`]+)`", line))
    }
    manifest = public_api_manifest()["cli"]
    for command in (*manifest["stable_commands"], *manifest["alpha_commands"]):
        if not any(row == command or row.startswith(f"{command} ") for row in documented_cli_rows):
            errors.append(f"docs/cli-reference.md:command_missing:{command}")

    _validate_markdown_links(errors)


def _pyproject() -> dict:
    return tomllib.loads(_read("pyproject.toml"))["project"]


def _dependency(project: dict, name: str) -> str:
    prefix = name
    for dependency in project.get("dependencies", []):
        text = str(dependency)
        if text.startswith(prefix):
            return text
    raise AssertionError(f"missing_dependency:{name}")


def _require(errors: list[str], path: str, expected: str) -> None:
    if expected not in _read(path):
        errors.append(f"{path}:missing:{expected}")


def _forbid(errors: list[str], path: str, forbidden: str) -> None:
    if forbidden in _read(path):
        errors.append(f"{path}:forbidden:{forbidden}")


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
    optional_dependencies = project.get("optional-dependencies") or {}

    if project["name"] != "rexecop":
        errors.append(f"distribution_name_mismatch:{project['name']}")
    if rexecop.__version__ != version:
        errors.append(f"package_version_mismatch:{rexecop.__version__}!={version}")
    if govengine_dep != EXPECTED_GOVENGINE:
        errors.append(f"govengine_dependency_mismatch:{govengine_dep}!={EXPECTED_GOVENGINE}")
    if sclite_dep != EXPECTED_SCLITE:
        errors.append(f"sclite_dependency_mismatch:{sclite_dep}!={EXPECTED_SCLITE}")
    if optional_dependencies.get("sclite") != [EXPECTED_SCLITE]:
        errors.append(
            "sclite_extra_dependency_mismatch:"
            f"{optional_dependencies.get('sclite')}!=[{EXPECTED_SCLITE}]"
        )
    if "tecrax" in optional_dependencies:
        errors.append("tecrax_extra_must_not_ship_in_v1_core")

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
    _require(errors, "docs/distribution.md", EXPECTED_GOVENGINE)
    _require(errors, "docs/distribution.md", EXPECTED_SCLITE)
    _require(errors, "docs/distribution.md", PUBLISHED_GOVENGINE)
    _require(errors, "docs/distribution.md", PUBLISHED_SCLITE)
    _require(errors, "docs/release-qualification-record.md", EXPECTED_GOVENGINE_STATUS)
    _forbid(
        errors,
        "docs/release-qualification-record.md",
        "source candidate; published `0.16.11`",
    )
    _require(errors, "docs/sclite-integration.md", EXPECTED_SCLITE)
    _forbid(errors, "docs/distribution.md", "govengine==0.17.0rc1")
    _forbid(errors, "docs/sclite-integration.md", "sclite-core==1.0.9")
    _require(errors, "OPERATOR_RUNBOOK.md", "scripts/validate_public_truth.py")
    _require(errors, "OPERATOR_LAB_RUNBOOK.md", "scripts/validate_public_truth.py")
    _require(errors, "README.md", "docs/stack-contract-compatibility.md")
    _require(errors, "README.md", "docs/public-api.md")
    _require(errors, "README.md", "rexecop.public_api.v1")
    _require(errors, "docs/public-api.md", "rexecop.public_api.v1")
    _require(errors, "docs/public-api.md", "alpha_root_requires_new_v1_root")
    _require(errors, "docs/public-api.md", "validate_m10_public_api_gate.py")
    _require(errors, "docs/storage-backends.md", "runtime_root_new_root_required")
    _require(errors, "docs/known-limitations.md", "Stack readiness labels")
    _require(errors, "docs/stack-contract-compatibility.md", "stack-contract-compatibility")
    _require(errors, "README.md", "contracts cli")
    _require(errors, "README.md", "format_matrix")
    _require(errors, "README.md", "exit_code_matrix")
    _require(errors, "README.md", "rexecop.cli_error.v0.1")
    _require(errors, "README.md", "observability diagnostics")
    _require(errors, "README.md", "structured logs")
    _require(errors, "docs/cli-reference.md", "rexecop.cli_contract_registry.v0.1")
    _require(errors, "docs/cli-reference.md", "stable_v1")
    _require(errors, "docs/cli-reference.md", "command_groups")
    _require(errors, "docs/cli-reference.md", "format_matrix")
    _require(errors, "docs/cli-reference.md", "exit_code_matrix")
    _require(errors, "docs/cli-reference.md", "rexecop.cli_error.v0.1")
    _require(errors, "docs/cli-reference.md", "rexecop.structured_log_event.v0.1")
    _require(errors, "docs/cli-reference.md", "rexecop.runtime_diagnostics.v0.1")
    _require(errors, "docs/cli-reference.md", "observability logs list")
    _require(
        errors,
        "docs/archive/pre-1.0-contract-qualification.md",
        "Historical M8 claim-to-code matrix",
    )
    _require(errors, "docs/release-qualification.md", "validate_artifact_install_smoke.py")
    _require(errors, "docs/release-qualification.md", "validate_clean_install_smoke.py")
    _require(errors, ".github/workflows/ci.yml", "validate_artifact_install_smoke.py")
    _require(errors, "docs/stack-contract-compatibility.md", EXPECTED_SCLITE)
    _require(errors, "docs/stack-contract-compatibility.md", EXPECTED_GOVENGINE)
    _require(
        errors,
        "docs/stack-contract-compatibility.md",
        f"`{EXPECTED_TECRAX_CONSUMER}`",
    )
    _require(errors, "docs/stack-contract-compatibility.md", "external source consumer")
    _require(errors, "docs/stack-contract-compatibility.md", "`mutation_ready` | false")
    _require(
        errors,
        "docs/stack-contract-compatibility.md",
        "typed_execution_governed_admission",
    )
    _require(
        errors,
        "docs/stack-contract-compatibility.md",
        GOVENGINE_SOURCE_REF,
    )
    _require(
        errors,
        "docs/govengine-integration.md",
        "Only the nested unchanged typed-execution v0.1",
    )
    _require(errors, "docs/govengine-integration.md", "actual GovEngine v0.2")
    _require(errors, "docs/connector-contract.md", "operator_wrapper")
    _require(
        errors,
        "docs/execution-contract.md",
        "rexecop.governed_admission_binding.v0.1",
    )
    _require(
        errors,
        "docs/known-limitations.md",
        "does not configure host authority adapters or make mutation ready",
    )
    _require(errors, "CHANGELOG.md", "do not enable `mutation_ready`")
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
    _require(errors, "README.md", "chain explain")
    _require(errors, "README.md", "reaction explain")
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
    _require(errors, "docs/govengine-integration.md", "profile-governance")
    _require(errors, "OPERATOR_RUNBOOK.md", "secrets doctor")
    _require(errors, "OPERATOR_RUNBOOK.md", "operations unavailable")
    _require(errors, "OPERATOR_RUNBOOK.md", "runtime recover")
    _require(errors, "OPERATOR_RUNBOOK.md", "validate_operator_journeys.py")
    _require(errors, "OPERATOR_RUNBOOK.md", "governance controls")
    _require(errors, "OPERATOR_LAB_RUNBOOK.md", "validate_operator_journeys.py")
    _require(errors, "docs/govengine-integration.md", "governance controls")
    _require(errors, "docs/runtime-recovery-ops.md", "REXECOP_STATIC_FIXTURE_FAILURES")
    _require(errors, "docs/stack-contract-compatibility.md", "validate_operator_journeys.py")
    _require(errors, "docs/known-limitations.md", "validate_operator_journeys.py")
    _require(errors, "docs/cli-reference.md", "governance controls")
    _require(errors, "docs/cli-reference.md", "validate_operator_journeys.py")
    for marker in M3_M4_DOC_INDEX_MARKERS:
        _require(errors, "OPERATOR_RUNBOOK.md", marker)
    _require(errors, "docs/release-qualification.md", "validate_first_run_smoke.py")
    _require(errors, "docs/release-qualification.md", "validate_operator_journeys.py")
    _require(
        errors,
        "docs/release-qualification.md",
        "validate_cross_repo_golden_fixture.py",
    )
    _require(errors, "docs/release-qualification.md", "validate_stack_contracts.py")
    _require(errors, ".github/workflows/ci.yml", "validate_first_run_smoke.py")
    _require(errors, ".github/workflows/ci.yml", "validate_operator_journeys.py")
    _require(errors, ".github/workflows/ci.yml", "validate_cross_repo_golden_fixture.py")
    _require(errors, "scripts/validate_stack_contracts.py", "stack_contracts_ok")
    _require(errors, "scripts/validate_profile_conformance.py", "profile_conformance_ok")
    _require(errors, "scripts/validate_first_run_smoke.py", "first_run_smoke_ok")
    _require(errors, "scripts/validate_operator_journeys.py", "operator_journeys_ok")
    _require(
        errors,
        "scripts/validate_cross_repo_golden_fixture.py",
        "cross_repo_golden_fixture_ok",
    )
    _require(errors, "docs/first-run.md", "rexecop examples materialize --output")
    _require(errors, "README.md", "rexecop examples materialize --output")
    _require(errors, "README.md", "rexecop --root /tmp/rexecop-first-run init --guided")
    _require(errors, "docs/cli-reference.md", "examples materialize --output NEW_DIR")
    _require(errors, "docs/distribution.md", "rexecop examples materialize --output NEW_DIR")
    _require(errors, "docs/architecture.md", "materializable package resource")
    _require(errors, "docs/known-limitations.md", "no network/distributed atomicity")
    _require(errors, "docs/release-qualification.md", "first-run materialization")
    _require(errors, "docs/release-qualification.md", "reviewed, immutable full-commit")
    _require(errors, "docs/release-qualification.md", "exact source identity only")
    _require(
        errors,
        "docs/release-qualification.md",
        "They do not prove package\ncompatibility",
    )
    _require(errors, "docs/release-qualification.md", "Tecrax\n`--no-deps` profile smoke")
    _require(errors, "docs/release-qualification.md", "does not auto-update to a latest")
    _require(errors, "docs/release-qualification.md", "Publish\nand repair workflows")
    _require(
        errors,
        ".github/workflows/ci.yml",
        "ref: ae91fb278879fefc965dc3fd51d86889385dc4f0",
    )
    _require(
        errors,
        ".github/workflows/ci.yml",
        f"ref: {SCLITE_SOURCE_REF}",
    )
    _require(
        errors,
        ".github/workflows/ci.yml",
        f"ref: {GOVENGINE_SOURCE_REF}",
    )
    _require(
        errors,
        "scripts/validate_first_run_smoke.py",
        'version = _run(empty_cwd, "version").strip()',
    )
    _require(errors, "scripts/validate_first_run_smoke.py", '"init", "--guided"')
    _require(errors, "scripts/validate_artifact_install_smoke.py", '[rexecop, "version"]')
    _require(
        errors,
        "scripts/validate_artifact_install_smoke.py",
        '"init", "--guided"',
    )
    _require(errors, "README.md", "docs/first-run.md")
    _require(errors, ".github/workflows/ci.yml", "python scripts/validate_public_truth.py")
    _require(errors, ".github/workflows/ci.yml", "python scripts/validate_stack_contracts.py")
    _require(errors, ".github/workflows/ci.yml", "python scripts/validate_profile_conformance.py")
    _require(errors, ".github/workflows/ci.yml", "rozmiarD/tecrax")
    _require(errors, ".github/workflows/ci.yml", "python -m build")
    _require(errors, ".github/workflows/ci.yml", "twine check")
    _require(errors, ".github/workflows/ci.yml", "validate_distribution.py")
    _require(errors, ".github/workflows/publish.yml", "workflow_dispatch")
    _require(
        errors,
        ".github/workflows/publish.yml",
        "pypa/gh-action-pypi-publish@cef221092ed1bacb1cc03d23a2d87d1d172e277b",
    )
    _require(errors, ".github/workflows/publish.yml", "name: pypi")
    _forbid(errors, ".github/workflows/publish.yml", "PYPI_" + "API_TOKEN")
    _forbid(errors, ".github/workflows/publish.yml", "TWINE_PASSWORD")
    _forbid(errors, ".github/workflows/publish.yml", "twine upload")
    _require(errors, ".github/workflows/publish.yml", "validate_distribution.py")
    _require(errors, ".github/workflows/publish.yml", "govengine_ref:")
    _require(errors, ".github/workflows/publish.yml", "sclite_ref:")
    _require(errors, ".github/workflows/publish.yml", "GOVSTACK_REPO_GOVENGINE:")
    _require(errors, ".github/workflows/publish.yml", "GOVSTACK_REPO_SCLITE:")
    _forbid(errors, ".github/workflows/publish.yml", "tecrax_ref:")
    _forbid(errors, ".github/workflows/publish.yml", "GOVSTACK_REPO_TECRAX:")
    _require(errors, ".github/workflows/publish.yml", "--previous-evidence")
    _require(
        errors,
        ".github/workflows/publish.yml",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    )
    _require(
        errors,
        ".github/workflows/publish.yml",
        "actions/attest-build-provenance@977bb373ede98d70efdf65b84cb5f73e068dcc2a",
    )
    _require(errors, ".github/workflows/publish.yml", "artifact-metadata: write")
    _require(errors, ".github/workflows/publish.yml", "dist/*.cdx.json")
    _require(errors, ".github/workflows/publish.yml", "--attestation-id")
    _require(errors, ".github/workflows/publish.yml", "--attestation-url")
    _require(errors, ".github/workflows/publish.yml", "Stage PyPI distributions")
    _require(
        errors,
        ".github/workflows/publish.yml",
        "cp dist/*.whl dist/*.tar.gz pypi-dist/",
    )
    _require(
        errors,
        ".github/workflows/publish.yml",
        'test "$(find pypi-dist -maxdepth 1 -type f | wc -l)" -eq 2',
    )
    _require(errors, ".github/workflows/publish.yml", "packages-dir: pypi-dist/")
    _forbid(errors, ".github/workflows/publish.yml", "packages-dir: dist")
    _require(errors, ".github/workflows/publish.yml", "git check-ref-format")
    _require(errors, ".github/workflows/publish.yml", "git rev-list -n 1")
    _require(errors, ".github/workflows/publish.yml", "git merge-base --is-ancestor")
    _require(errors, ".github/workflows/publish.yml", "fetch-depth: 0")
    _require(
        errors,
        ".github/workflows/publish.yml",
        "Checkout immutable RExecOP release source",
    )
    _require(errors, ".github/workflows/publish.yml", "refs/tags/v${{ inputs.version }}")
    _require(
        errors,
        ".github/workflows/publish.yml",
        '--release-commit "${{ steps.release_source.outputs.commit }}"',
    )
    _require(
        errors,
        ".github/workflows/publish.yml",
        "Install release validation dependencies",
    )
    _require(
        errors,
        ".github/workflows/publish.yml",
        "python -m pip install -e .govstack/sclite",
    )
    _require(
        errors,
        ".github/workflows/publish.yml",
        "python -m pip install -e .govstack/govengine",
    )
    _require(
        errors,
        ".github/workflows/publish.yml",
        'python -m pip install -e ".[dev]"',
    )
    _require(errors, ".github/workflows/publish.yml", "gh release download")
    _require(errors, ".github/workflows/publish.yml", "gh release create")
    _require(
        errors,
        ".github/workflows/publish.yml",
        "github_release_prerelease_flag.py",
    )
    _require(
        errors,
        ".github/workflows/publish.yml",
        'release_args+=("$prerelease_flag")',
    )
    _require(errors, ".github/workflows/publish.yml", "--verify-tag")
    _forbid(errors, ".github/workflows/publish.yml", "HEAD:release-evidence")
    _forbid(errors, ".github/workflows/publish.yml", "refs/heads/release-evidence")
    _forbid(errors, ".github/workflows/publish.yml", "git worktree")
    _require(errors, ".github/workflows/publish.yml", 'test "$GITHUB_REF" = "refs/heads/main"')
    _require(errors, ".github/workflows/publish.yml", "^[0-9a-f]{40}$")
    _require(errors, ".github/workflows/repair-release-evidence.yml", "--no-binary rexecop")
    _require(
        errors,
        ".github/workflows/repair-release-evidence.yml",
        "python -m pip install --upgrade pip packaging pip-audit cyclonedx-bom",
    )
    _require(
        errors,
        ".github/workflows/repair-release-evidence.yml",
        "validate_supply_chain_gate.py dist --version",
    )
    _require(errors, ".github/workflows/repair-release-evidence.yml", "dist/*.cdx.json")
    _require(
        errors,
        ".github/workflows/repair-release-evidence.yml",
        "actions/attest-build-provenance@977bb373ede98d70efdf65b84cb5f73e068dcc2a",
    )
    _require(errors, ".github/workflows/repair-release-evidence.yml", "git rev-list -n 1")
    _require(errors, ".github/workflows/repair-release-evidence.yml", "gh release upload")
    _require(
        errors,
        ".github/workflows/repair-release-evidence.yml",
        "github_release_prerelease_flag.py",
    )
    _require(
        errors,
        ".github/workflows/repair-release-evidence.yml",
        'release_args+=("$prerelease_flag")',
    )
    _require(errors, ".github/workflows/repair-release-evidence.yml", "--clobber")
    _forbid(errors, ".github/workflows/repair-release-evidence.yml", "git worktree")
    _require(errors, "docs/release-evidence/README.md", "rexecop.release_evidence.v2")
    _require(errors, "docs/release-evidence/README.md", "gh attestation verify")
    _require(errors, "docs/release-evidence/README.md", "GitHub Release assets")
    _require(errors, "docs/distribution.md", "validate_m10_release_gate.py --live-github")
    _require(errors, "docs/release-qualification.md", "validate_m10_release_gate.py")

    init_text = _read("src/rexecop/__init__.py")
    if f'__version__ = "{version}"' not in init_text:
        errors.append("src/rexecop/__init__.py:missing_version_literal")

    _assert_pypi_docs(errors, version)
    _validate_document_semantics(errors)

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
        f"{EXPECTED_GOVENGINE}:{EXPECTED_SCLITE}:tecrax_consumer={EXPECTED_TECRAX_CONSUMER}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
