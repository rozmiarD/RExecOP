#!/usr/bin/env python3
"""Validate M10 artifact evidence and optional live GitHub release protection."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "rozmiarD/RExecOP"
REQUIRED_MAIN_CHECKS = {
    "test (3.11)",
    "test (3.12)",
    "test (3.13)",
    "package-dry-run",
}
RELEASE_TAG_PATTERN = "refs/tags/v*"


def validate_live_release_protection(
    load_api: Callable[[str], Any],
) -> list[str]:
    errors: list[str] = []
    branch = load_api(f"repos/{REPOSITORY}/branches/main/protection")
    checks = branch.get("required_status_checks") if isinstance(branch, dict) else None
    contexts = set(checks.get("contexts") or []) if isinstance(checks, dict) else set()
    if not isinstance(checks, dict) or checks.get("strict") is not True:
        errors.append("github_main_strict_checks_missing")
    missing_checks = sorted(REQUIRED_MAIN_CHECKS - contexts)
    if missing_checks:
        errors.append(f"github_main_required_checks_missing:{','.join(missing_checks)}")

    environment = load_api(f"repos/{REPOSITORY}/environments/pypi")
    policy = environment.get("deployment_branch_policy") if isinstance(environment, dict) else None
    if not isinstance(policy, dict) or policy.get("protected_branches") is not True:
        errors.append("github_pypi_protected_branches_missing")
    rules = environment.get("protection_rules") if isinstance(environment, dict) else None
    if not isinstance(rules, list) or not any(
        isinstance(item, dict) and item.get("type") == "branch_policy" for item in rules
    ):
        errors.append("github_pypi_branch_policy_rule_missing")

    rulesets = load_api(f"repos/{REPOSITORY}/rulesets")
    matching = (
        [
            item
            for item in rulesets
            if isinstance(item, dict) and item.get("name") == "Protect release tags"
        ]
        if isinstance(rulesets, list)
        else []
    )
    if not matching:
        errors.append("github_release_tag_ruleset_missing")
        return errors
    ruleset_id = matching[0].get("id")
    ruleset = load_api(f"repos/{REPOSITORY}/rulesets/{ruleset_id}")
    conditions = ruleset.get("conditions") if isinstance(ruleset, dict) else None
    ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
    includes = set(ref_name.get("include") or []) if isinstance(ref_name, dict) else set()
    rule_types = {
        str(item.get("type") or "")
        for item in ruleset.get("rules") or []
        if isinstance(item, dict)
    } if isinstance(ruleset, dict) else set()
    if ruleset.get("target") != "tag" or ruleset.get("enforcement") != "active":
        errors.append("github_release_tag_ruleset_inactive")
    if RELEASE_TAG_PATTERN not in includes:
        errors.append("github_release_tag_pattern_missing")
    if not {"deletion", "update"}.issubset(rule_types):
        errors.append("github_release_tag_immutability_missing")
    if ruleset.get("bypass_actors"):
        errors.append("github_release_tag_bypass_present")
    return errors


def _gh_api(path: str) -> Any:
    result = subprocess.run(
        ["gh", "api", path],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"github_api_failed:{path}")
    return json.loads(result.stdout)


def _run(command: list[str]) -> int:
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-github", action="store_true")
    args = parser.parse_args(argv)

    if _run([sys.executable, "scripts/validate_workflow_security.py"]):
        return 1
    if _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_release_evidence.py",
            "tests/test_public_index_release_smoke.py",
            "tests/test_release_train_preflight.py",
            "tests/test_supply_chain_gate.py",
            "tests/test_m10_release_gate.py",
        ]
    ):
        return 1
    if args.live_github:
        try:
            errors = validate_live_release_protection(_gh_api)
        except (RuntimeError, json.JSONDecodeError) as exc:
            print(f"m10_release_gate_error:{exc}", file=sys.stderr)
            return 1
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
    print(
        "m10_release_gate_ok:action_pins=OK:oidc=OK:wheel_sdist_identity=OK:"
        "sbom_binding=OK:artifact_attestation=OK:release_evidence_v2=OK:"
        f"github_protection={'OK' if args.live_github else 'NOT_CHECKED'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
