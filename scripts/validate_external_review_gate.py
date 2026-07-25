#!/usr/bin/env python3
"""Release-candidate external/security review gate for M8.5."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
REVIEW_DIR = ROOT / "docs" / "release-security-review"
LEGACY_REVIEW_SCHEMA = "rexecop.release_security_review.v0.1"
SOURCE_BOUND_REVIEW_SCHEMA = "rexecop.release_security_review.v0.2"
REVIEW_SCHEMAS = frozenset({LEGACY_REVIEW_SCHEMA, SOURCE_BOUND_REVIEW_SCHEMA})
ALLOWED_REVIEW_MODES = frozenset({"independent_review", "solo_reviewed_alpha_risk"})
SOURCE_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_SURFACES = frozenset(
    {
        "governance_admission_binding",
        "mutation_gates",
        "connector_output_safety",
        "release_train_scripts",
        "supply_chain_workflow",
    }
)


def current_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def review_record_path(version: str) -> Path:
    return REVIEW_DIR / f"{version}.json"


def load_review_record(version: str) -> dict[str, Any]:
    path = review_record_path(version)
    if not path.is_file():
        raise ValueError(f"review_record_missing:{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"review_record_not_object:{path}")
    return payload


def _parsed_version(version: str) -> Version | None:
    try:
        return Version(version)
    except InvalidVersion:
        return None


def _requires_source_bound_schema(version: str) -> bool:
    parsed = _parsed_version(version)
    return (
        parsed is not None
        and parsed >= Version("1.0.0rc1")
        and version != "1.0.0rc1"
    )


def _requires_independent_review(version: str) -> bool:
    parsed = _parsed_version(version)
    return parsed is not None and parsed >= Version("1.0.0rc1")


def _requires_source_bound_review(version: str) -> bool:
    parsed = _parsed_version(version)
    return parsed is not None and parsed >= Version("1.0.0") and not parsed.is_prerelease


def validate_review_record(
    payload: dict[str, Any],
    *,
    version: str,
) -> list[str]:
    errors: list[str] = []
    if _parsed_version(version) is None:
        errors.append(f"review_version_invalid:{version}")
    schema = str(payload.get("schema") or "")
    if schema not in REVIEW_SCHEMAS:
        errors.append(f"review_schema_invalid:{schema}")
    elif _requires_source_bound_schema(version) and schema != SOURCE_BOUND_REVIEW_SCHEMA:
        errors.append(f"source_bound_review_schema_required:{version}")
    if str(payload.get("version") or "") != version:
        errors.append(f"review_version_mismatch:{payload.get('version')}!={version}")
    mode = str(payload.get("review_mode") or "")
    if mode not in ALLOWED_REVIEW_MODES:
        errors.append(f"review_mode_invalid:{mode}")
    if _requires_independent_review(version) and mode != "independent_review":
        errors.append(f"independent_review_required:{version}")
    reviewer = str(payload.get("reviewer_ref") or "").strip()
    if not reviewer:
        errors.append("reviewer_ref_missing")
    reviewed_at = str(payload.get("reviewed_at") or "").strip()
    if not reviewed_at:
        errors.append("reviewed_at_missing")
    surfaces = payload.get("surfaces")
    if not isinstance(surfaces, list):
        errors.append("surfaces_not_list")
        return errors
    declared = {str(item).strip() for item in surfaces if str(item).strip()}
    missing = sorted(REQUIRED_SURFACES - declared)
    if missing:
        errors.append(f"review_surfaces_missing:{','.join(missing)}")
    if mode == "solo_reviewed_alpha_risk":
        notes = str(payload.get("notes") or "").strip()
        if not notes:
            errors.append("solo_review_notes_required")
    reviewed_source_commit = str(payload.get("reviewed_source_commit") or "").strip()
    if schema == SOURCE_BOUND_REVIEW_SCHEMA or _requires_source_bound_review(version):
        if not SOURCE_COMMIT_PATTERN.fullmatch(reviewed_source_commit):
            errors.append("reviewed_source_commit_invalid")
    return errors


def validate_release_commit_binding(
    payload: dict[str, Any],
    *,
    version: str,
    release_commit: str,
) -> list[str]:
    """Bind a reviewed source commit to a release-evidence-only tag commit."""

    expected = release_commit.strip()
    if not SOURCE_COMMIT_PATTERN.fullmatch(expected):
        return ["release_commit_invalid"]
    reviewed = str(payload.get("reviewed_source_commit") or "").strip()
    if not SOURCE_COMMIT_PATTERN.fullmatch(reviewed):
        return ["reviewed_source_commit_invalid"]
    if reviewed == expected:
        return []
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", reviewed, expected],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if ancestor.returncode != 0:
        return [f"reviewed_source_commit_not_ancestor:{reviewed}!={expected}"]
    changed = subprocess.run(
        ["git", "diff", "--name-only", reviewed, expected],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if changed.returncode != 0:
        return ["reviewed_source_commit_diff_failed"]
    allowed_path = str(review_record_path(version).relative_to(ROOT))
    changed_paths = sorted(line.strip() for line in changed.stdout.splitlines() if line.strip())
    if changed_paths != [allowed_path]:
        return [
            "reviewed_source_commit_unreviewed_delta:"
            + ",".join(changed_paths or ["<empty>"])
        ]
    return []


def collect_errors(
    *,
    version: str | None = None,
    release_commit: str | None = None,
) -> list[str]:
    resolved = version or current_version()
    errors: list[str] = []
    try:
        payload = load_review_record(resolved)
    except ValueError as exc:
        errors.append(str(exc))
        return errors
    errors.extend(
        validate_review_record(
            payload,
            version=resolved,
        )
    )
    if release_commit is not None:
        errors.extend(
            validate_release_commit_binding(
                payload,
                version=resolved,
                release_commit=release_commit,
            )
        )
    return errors


def success_line(version: str, review_mode: str) -> str:
    return f"external_review_gate_ok:rexecop=={version}:mode={review_mode}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate release security review record for a version."
    )
    parser.add_argument(
        "--version",
        default="",
        help="Package version (defaults to pyproject.toml project.version).",
    )
    parser.add_argument(
        "--release-commit",
        default="",
        help=(
            "Immutable tagged release commit. It must equal the reviewed source or "
            "differ only by this version's review record."
        ),
    )
    args = parser.parse_args(argv)
    version = args.version.strip() or current_version()
    release_commit = args.release_commit.strip() or None
    errors = collect_errors(version=version, release_commit=release_commit)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    payload = load_review_record(version)
    print(success_line(version, str(payload["review_mode"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
