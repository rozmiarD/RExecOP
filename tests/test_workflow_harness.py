from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.profile.discoverability import (
    run_profile_developer_check,
    run_profile_workflow_harness_report,
)
from rexecop.profile.workflow_harness import (
    HARNESS_CHECK_IDS,
    PROFILE_WORKFLOW_HARNESS_SCHEMA,
    resolve_harness_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PROFILE = ROOT / "examples/profiles/runtime-fixture/profile.yaml"
REGISTERED_PROFILE = "tecrax"

runner = CliRunner()


def test_resolve_harness_fixture_for_runtime_fixture_profile() -> None:
    fixture = resolve_harness_fixture(FIXTURE_PROFILE)

    assert fixture is not None
    assert fixture.readonly_intent == "inspect_fixture_state"
    assert fixture.blocked_intent == "apply_fixture_change"
    assert fixture.environment_path.name == "runtime-fixture.policy.example.yaml"


def test_resolve_harness_fixture_skips_registered_profiles_without_fixture() -> None:
    assert resolve_harness_fixture(REGISTERED_PROFILE) is None


def test_workflow_harness_passes_for_runtime_fixture(tmp_path: Path) -> None:
    result = run_profile_workflow_harness_report(
        FIXTURE_PROFILE,
        store_root=tmp_path / "harness",
    )

    assert result["schema"] == PROFILE_WORKFLOW_HARNESS_SCHEMA
    assert result["status"] == "passed"
    check_ids = [check["id"] for check in result["checks"]]
    assert check_ids == list(HARNESS_CHECK_IDS)
    assert all(check["status"] == "passed" for check in result["checks"])
    dry_run = next(check for check in result["checks"] if check["id"] == "dry_run_fixture")
    assert dry_run["details"]["intent"] == "inspect_fixture_state"
    bundle = next(check for check in result["checks"] if check["id"] == "sclite_bundle_shape")
    assert bundle["details"]["verdict"] == "pass"


def test_workflow_harness_skips_for_tecrax() -> None:
    result = run_profile_workflow_harness_report(REGISTERED_PROFILE)

    assert result["status"] == "skipped"
    assert result["skip_reason"] == "no_fixture_environment_configured"
    assert all(check["status"] == "skipped" for check in result["checks"])


def test_developer_check_includes_workflow_harness() -> None:
    tecrax = run_profile_developer_check(REGISTERED_PROFILE, track="readonly")
    assert tecrax["workflow_harness"]["status"] == "skipped"

    fixture = run_profile_developer_check(FIXTURE_PROFILE, track="readonly")
    assert fixture["workflow_harness"]["status"] == "passed"


def test_cli_profile_harness_emits_json_for_runtime_fixture(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    result = runner.invoke(
        app,
        [
            "--root",
            str(root),
            "profile",
            "harness",
            "--profile",
            str(FIXTURE_PROFILE),
        ],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["schema"] == PROFILE_WORKFLOW_HARNESS_SCHEMA


def test_cli_profile_harness_exits_nonzero_for_blocked_mutation(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    env = yaml.safe_load(
        (ROOT / "examples/environments/runtime-fixture.example.yaml").read_text(encoding="utf-8")
    )
    env["environment"]["policy_pack"] = {
        "policy_id": "allow-all-mutations",
        "version": "test",
        "rules": [
            {
                "rule_id": "allow-all",
                "effect": "allow",
                "conditions": {"action.category": "operation"},
            }
        ],
    }
    env_path = tmp_path / "permissive-env.yaml"
    env_path.write_text(yaml.safe_dump(env, sort_keys=False), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--root",
            str(root),
            "profile",
            "harness",
            "--profile",
            str(FIXTURE_PROFILE),
            "--env",
            str(env_path),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    blocked = next(check for check in payload["checks"] if check["id"] == "policy_blocked_path")
    assert blocked["status"] == "failed"