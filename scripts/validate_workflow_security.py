#!/usr/bin/env python3
"""Fail closed on moving GitHub Action references in release workflows."""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
USES = re.compile(r"^\s*(?:-\s+)?uses:\s+([^@\s]+)@([^\s#]+)", re.MULTILINE)
REQUIRED_PINS = {
    "actions/checkout": "df4cb1c069e1874edd31b4311f1884172cec0e10",
    "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/attest-build-provenance": (
        "977bb373ede98d70efdf65b84cb5f73e068dcc2a"
    ),
    "pypa/gh-action-pypi-publish": (
        "cef221092ed1bacb1cc03d23a2d87d1d172e277b"
    ),
}
EXPECTED_CI_SIBLING_CHECKOUTS = (
    (
        "test",
        "rozmiarD/tecrax",
        "ci-deps/tecrax",
        "ae91fb278879fefc965dc3fd51d86889385dc4f0",
    ),
    (
        "test",
        "rozmiarD/SCLite",
        "ci-deps/sclite",
        "0b90c21569ea908ba7ddb468cd1ab6126342924f",
    ),
    (
        "test",
        "rozmiarD/GovEngine",
        "ci-deps/govengine",
        "0826accff407fdbc10df420803ff49cdd5818870",
    ),
    (
        "package-dry-run",
        "rozmiarD/SCLite",
        "ci-deps/sclite",
        "0b90c21569ea908ba7ddb468cd1ab6126342924f",
    ),
    (
        "package-dry-run",
        "rozmiarD/GovEngine",
        "ci-deps/govengine",
        "0826accff407fdbc10df420803ff49cdd5818870",
    ),
)


def validate_workflow_security() -> dict[str, int]:
    paths = sorted(WORKFLOWS.glob("*.yml"))
    if not paths:
        raise AssertionError("workflow_security_missing_workflows")
    action_count = 0
    seen: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for action, reference in USES.findall(text):
            action_count += 1
            seen.add(action)
            if not FULL_SHA.fullmatch(reference):
                raise AssertionError(
                    f"workflow_action_not_pinned:{path.name}:{action}@{reference}"
                )
            required = REQUIRED_PINS.get(action)
            if required is not None and reference != required:
                raise AssertionError(
                    f"workflow_action_unreviewed_pin:{path.name}:{action}@{reference}"
                )
    missing = sorted(set(REQUIRED_PINS) - seen)
    if missing:
        raise AssertionError(f"workflow_required_action_missing:{','.join(missing)}")
    sibling_checkout_count = _validate_ci_sibling_checkouts()
    publish = (WORKFLOWS / "publish.yml").read_text(encoding="utf-8")
    for marker in (
        "name: pypi",
        "id-token: write",
        "artifact-metadata: write",
        "pypa/gh-action-pypi-publish@",
        "dist/*.cdx.json",
        "steps.release_subject_attestation.outputs.attestation-id",
        "steps.release_subject_attestation.outputs.attestation-url",
        "Stage PyPI distributions",
        "cp dist/*.whl dist/*.tar.gz pypi-dist/",
        'test "$(find pypi-dist -maxdepth 1 -type f | wc -l)" -eq 2',
        "packages-dir: pypi-dist/",
        "git check-ref-format",
        "git rev-list -n 1",
        "git merge-base --is-ancestor",
        "fetch-depth: 0",
        "Checkout immutable RExecOP release source",
        "refs/tags/v${{ inputs.version }}",
        "--release-commit \"${{ steps.release_source.outputs.commit }}\"",
        "Install release validation dependencies",
        "python -m pip install -e .govstack/sclite",
        "python -m pip install -e .govstack/govengine",
        'python -m pip install -e ".[dev]"',
        "gh release download",
        "gh release create",
        "github_release_prerelease_flag.py",
        'release_args+=("$prerelease_flag")',
        "--verify-tag",
        'default: "1.0.0rc1"',
        'default: "2470373c6384c284ab48df7ce763f0938797d155"',
    ):
        if marker not in publish:
            raise AssertionError(f"workflow_publish_missing:{marker}")
    for forbidden in (
        "PYPI_" + "API_TOKEN",
        "TWINE_PASSWORD",
        "twine upload",
        "skip-existing:",
        "packages-dir: dist",
        "HEAD:release-evidence",
        "refs/heads/release-evidence",
        "git worktree",
    ):
        if forbidden in publish:
            raise AssertionError(f"workflow_publish_unsafe_setting:{forbidden}")
    repair = (WORKFLOWS / "repair-release-evidence.yml").read_text(encoding="utf-8")
    for marker in (
        "artifact-metadata: write",
        "python -m pip install --upgrade pip packaging pip-audit cyclonedx-bom",
        "validate_supply_chain_gate.py dist --version",
        "dist/*.cdx.json",
        "steps.release_subject_attestation.outputs.attestation-id",
        "steps.release_subject_attestation.outputs.attestation-url",
        "git check-ref-format",
        "git rev-list -n 1",
        "gh release upload",
        "github_release_prerelease_flag.py",
        'release_args+=("$prerelease_flag")',
        "--clobber",
        "--verify-tag",
    ):
        if marker not in repair:
            raise AssertionError(f"workflow_repair_missing:{marker}")
    for forbidden in ("HEAD:release-evidence", "refs/heads/release-evidence", "git worktree"):
        if forbidden in repair:
            raise AssertionError(f"workflow_repair_legacy_branch_setting:{forbidden}")
    return {
        "workflows": len(paths),
        "actions": action_count,
        "sibling_checkouts": sibling_checkout_count,
    }


def _validate_ci_sibling_checkouts() -> int:
    ci_path = WORKFLOWS / "ci.yml"
    try:
        document = yaml.safe_load(ci_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AssertionError("workflow_ci_invalid_yaml") from exc
    if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
        raise AssertionError("workflow_ci_jobs_missing")

    actual: list[tuple[str, str, str, str]] = []
    for job_name, job in document["jobs"].items():
        if not isinstance(job_name, str) or not isinstance(job, dict):
            raise AssertionError("workflow_ci_job_invalid")
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            raise AssertionError(f"workflow_ci_steps_invalid:{job_name}")
        for step in steps:
            if not isinstance(step, dict) or not str(step.get("uses", "")).startswith(
                "actions/checkout@"
            ):
                continue
            options = step.get("with", {})
            if not isinstance(options, dict):
                raise AssertionError(f"workflow_ci_checkout_options_invalid:{job_name}")
            if "repository" not in options and "path" not in options:
                continue
            repository = options.get("repository")
            path = options.get("path")
            reference = options.get("ref")
            if not all(isinstance(item, str) for item in (repository, path, reference)):
                raise AssertionError(f"workflow_ci_sibling_checkout_invalid:{job_name}")
            if not FULL_SHA.fullmatch(reference):
                raise AssertionError(
                    f"workflow_ci_sibling_ref_not_literal_sha:{job_name}:{repository}:{path}"
                )
            actual.append((job_name, repository, path, reference))

    if Counter(actual) != Counter(EXPECTED_CI_SIBLING_CHECKOUTS):
        raise AssertionError("workflow_ci_sibling_checkout_mismatch")
    return len(actual)


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser().parse_args(argv)
    report = validate_workflow_security()
    print(
        "workflow_security_ok:"
        f"workflows={report['workflows']}:actions={report['actions']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
