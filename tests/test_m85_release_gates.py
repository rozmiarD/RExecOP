from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from invariant_scope import INVARIANT_TEST_MODULE, INVARIANT_THEMES

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_invariant_module_exists() -> None:
    assert (REPO_ROOT / "tests" / f"{INVARIANT_TEST_MODULE}.py").is_file()


def test_invariant_themes_are_documented() -> None:
    assert len(INVARIANT_THEMES) >= 5


@pytest.mark.invariant
def test_validate_stack_invariants_gate_passes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/validate_stack_invariants.py"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "stack_invariants_ok" in result.stdout


def test_validate_external_review_gate_passes_for_last_published_line() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_external_review_gate.py",
            "--version",
            "1.0.0rc1",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "external_review_gate_ok" in result.stdout


def test_validate_external_review_gate_rejects_missing_record(tmp_path: Path) -> None:
    script = REPO_ROOT / "scripts" / "validate_external_review_gate.py"
    result = subprocess.run(
        [sys.executable, str(script), "--version", "9.9.9-not-published"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "review_record_missing" in result.stderr


def test_validate_external_review_gate_rejects_incomplete_surfaces() -> None:
    record = {
        "schema": "rexecop.release_security_review.v0.1",
        "version": "0.0.0-test",
        "review_mode": "solo_reviewed_alpha_risk",
        "reviewed_at": "2026-07-08",
        "reviewer_ref": "reviewer:test",
        "surfaces": ["governance_admission_binding"],
        "notes": "incomplete test fixture",
    }
    module = _load_gate_module("validate_external_review_gate")
    errors = module.validate_review_record(record, version="0.0.0-test")
    assert any(error.startswith("review_surfaces_missing:") for error in errors)


def test_release_candidate_requires_independent_review() -> None:
    module = _load_gate_module("validate_external_review_gate")
    record = {
        "schema": "rexecop.release_security_review.v0.1",
        "version": "1.0.0rc2",
        "review_mode": "solo_reviewed_alpha_risk",
        "reviewed_at": "2026-07-25",
        "reviewer_ref": "reviewer:test",
        "surfaces": sorted(module.REQUIRED_SURFACES),
        "notes": "test fixture",
    }

    errors = module.validate_review_record(record, version="1.0.0rc2")

    assert "independent_review_required:1.0.0rc2" in errors
    assert "source_bound_review_schema_required:1.0.0rc2" in errors


def test_final_release_requires_source_bound_schema() -> None:
    module = _load_gate_module("validate_external_review_gate")
    record = {
        "schema": "rexecop.release_security_review.v0.1",
        "version": "1.0.0",
        "review_mode": "independent_review",
        "reviewed_at": "2026-07-25",
        "reviewer_ref": "reviewer:test",
        "reviewed_source_commit": "a" * 40,
        "surfaces": sorted(module.REQUIRED_SURFACES),
        "notes": "test fixture",
    }

    errors = module.validate_review_record(record, version="1.0.0")

    assert "source_bound_review_schema_required:1.0.0" in errors


def test_external_review_rejects_invalid_version() -> None:
    module = _load_gate_module("validate_external_review_gate")
    record = {
        "schema": "rexecop.release_security_review.v0.1",
        "version": "not-a-version",
        "review_mode": "solo_reviewed_alpha_risk",
        "reviewed_at": "2026-07-25",
        "reviewer_ref": "reviewer:test",
        "surfaces": sorted(module.REQUIRED_SURFACES),
        "notes": "test fixture",
    }

    errors = module.validate_review_record(record, version="not-a-version")

    assert "review_version_invalid:not-a-version" in errors


def test_release_commit_accepts_review_evidence_only_delta() -> None:
    module = _load_gate_module("validate_external_review_gate")
    record = module.load_review_record("1.0.0rc1")

    errors = module.validate_release_commit_binding(
        {
            **record,
            "reviewed_source_commit": "03b8a160af5e8aed2cb4a645ee489ad277f3fa9a",
        },
        version="1.0.0rc1",
        release_commit="6b1f011a7e70f93a40858fbf7d6537c33572f558",
    )

    assert errors == []


def test_release_commit_rejects_unreviewed_delta() -> None:
    module = _load_gate_module("validate_external_review_gate")
    record = module.load_review_record("1.0.0rc1")
    release_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    errors = module.validate_release_commit_binding(
        {
            **record,
            "reviewed_source_commit": "03b8a160af5e8aed2cb4a645ee489ad277f3fa9a",
        },
        version="1.0.0rc1",
        release_commit=release_commit,
    )

    assert any(error.startswith("reviewed_source_commit_unreviewed_delta:") for error in errors)


def _load_gate_module(name: str):
    import importlib.util

    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"rexecop_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
