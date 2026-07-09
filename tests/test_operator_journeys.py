from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.connectors.static_fixture import StaticFixtureRuntime
from rexecop.governance.operator_surface import (
    GOVERNANCE_CONTROLS_SCHEMA,
    collect_governance_controls,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_operator_journeys.py"
PROFILE = ROOT / "examples/profiles/runtime-fixture/profile.yaml"

runner = CliRunner()


def test_collect_governance_controls_passes() -> None:
    payload = collect_governance_controls(profile=PROFILE, track="readonly")

    assert payload["schema"] == GOVERNANCE_CONTROLS_SCHEMA
    assert payload["status"] == "passed"
    assert payload["control_ids"]
    assert payload["profile_governance"] is not None
    assert payload["profile_governance"]["profile"] == "runtime_fixture"


def test_cli_governance_controls_emits_catalog() -> None:
    result = runner.invoke(app, ["governance", "controls", "--profile", str(PROFILE)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema"] == GOVERNANCE_CONTROLS_SCHEMA
    assert payload["profile_governance"] is not None


def test_static_fixture_env_failures_apply_once_per_process() -> None:
    StaticFixtureRuntime.clear_failures()
    env = {
        "REXECOP_STATIC_FIXTURE_FAILURES": json.dumps(
            {
                "fixture_source:read_fixture_state": {
                    "count": 1,
                    "error_class": "transient_connector_error",
                }
            }
        )
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from rexecop.connectors.static_fixture import StaticFixtureRuntime; "
            "StaticFixtureRuntime._ensure_env_failures_loaded(); "
            "print(StaticFixtureRuntime._failure_counts)",
        ],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
        env={**dict(__import__("os").environ), **env},
    )

    assert result.returncode == 0, result.stderr
    assert "fixture_source" in result.stdout


@pytest.mark.delivery
def test_validate_operator_journeys_smoke() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "operator_journeys_ok" in result.stdout
