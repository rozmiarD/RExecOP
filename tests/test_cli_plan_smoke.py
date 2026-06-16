from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from rexecop.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"

runner = CliRunner()


def test_cli_plan_smoke(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "plan",
            "--profile",
            str(PROFILE),
            "--env",
            str(ENVIRONMENT),
            "--intent",
            "check_backup_status",
            "--target",
            "all_critical_vms",
            "--mode",
            "dry_run",
        ],
    )
    assert result.exit_code == 0
    operation_id = result.stdout.strip()
    assert operation_id.startswith("op-")

    status = runner.invoke(app, ["status", "--operation", operation_id])
    assert status.exit_code == 0
    payload = json.loads(status.stdout)
    assert payload["state"] == "planned"

    history = runner.invoke(app, ["history", "--operation", operation_id])
    assert history.exit_code == 0
    history_payload = json.loads(history.stdout)
    assert history_payload["operation_id"] == operation_id
    assert history_payload["evidence_events"]
