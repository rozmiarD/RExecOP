from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from rexecop.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"

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
            "inspect_fixture_state",
            "--target",
            "fixture-target",
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
    assert payload["schema"] == "rexecop.operation_status.v0.1"
    assert payload["state"] == "planned"

    history = runner.invoke(app, ["history", "--operation", operation_id])
    assert history.exit_code == 0
    history_payload = json.loads(history.stdout)
    assert history_payload["operation_id"] == operation_id
    assert history_payload["evidence_events"]


def test_cli_root_isolates_runtime_state(tmp_path: Path) -> None:
    root_a = tmp_path / "runtime-a"
    root_b = tmp_path / "runtime-b"
    result = runner.invoke(
        app,
        [
            "--root",
            str(root_a),
            "plan",
            "--profile",
            str(PROFILE),
            "--env",
            str(ENVIRONMENT),
            "--intent",
            "inspect_fixture_state",
            "--target",
            "fixture-target",
            "--mode",
            "dry_run",
        ],
    )

    assert result.exit_code == 0, result.output
    operation_id = result.stdout.strip()
    assert (root_a / "operations" / f"{operation_id}.json").is_file()
    assert not (root_b / "operations" / f"{operation_id}.json").exists()

    missing = runner.invoke(
        app,
        ["--root", str(root_b), "status", "--operation", operation_id],
    )
    assert missing.exit_code == 1
    assert "operation not found" in missing.output

    status = runner.invoke(
        app,
        ["--root", str(root_a), "status", "--operation", operation_id],
    )
    assert status.exit_code == 0, status.output
    payload = json.loads(status.stdout)
    assert payload["schema"] == "rexecop.operation_status.v0.1"
    assert payload["operation_id"] == operation_id


def test_cli_root_envvar_and_explicit_precedence(tmp_path: Path) -> None:
    env_root = tmp_path / "env-root"
    explicit_root = tmp_path / "explicit-root"
    env = {"REXECOP_ROOT": str(env_root)}
    result = runner.invoke(
        app,
        [
            "--root",
            str(explicit_root),
            "plan",
            "--profile",
            str(PROFILE),
            "--env",
            str(ENVIRONMENT),
            "--intent",
            "inspect_fixture_state",
            "--target",
            "fixture-target",
            "--mode",
            "dry_run",
        ],
        env=env,
    )

    assert result.exit_code == 0, result.output
    operation_id = result.stdout.strip()
    assert (explicit_root / "operations" / f"{operation_id}.json").is_file()
    assert not env_root.exists()

    env_result = runner.invoke(
        app,
        [
            "plan",
            "--profile",
            str(PROFILE),
            "--env",
            str(ENVIRONMENT),
            "--intent",
            "inspect_fixture_state",
            "--target",
            "fixture-target",
            "--mode",
            "dry_run",
        ],
        env=env,
    )

    assert env_result.exit_code == 0, env_result.output
    env_operation_id = env_result.stdout.strip()
    assert (env_root / "operations" / f"{env_operation_id}.json").is_file()


def test_cli_named_instance_isolates_default_runtime_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "--instance",
            "alpha",
            "plan",
            "--profile",
            str(PROFILE),
            "--env",
            str(ENVIRONMENT),
            "--intent",
            "inspect_fixture_state",
            "--target",
            "fixture-target",
            "--mode",
            "dry_run",
        ],
    )

    assert result.exit_code == 0, result.output
    operation_id = result.stdout.strip()
    instance_root = tmp_path / ".rexecop" / "instances" / "alpha"
    assert (instance_root / "operations" / f"{operation_id}.json").is_file()
    assert not (tmp_path / ".rexecop" / "operations" / f"{operation_id}.json").exists()


def test_cli_explicit_root_wins_over_named_instance(tmp_path: Path) -> None:
    explicit_root = tmp_path / "explicit-root"

    result = runner.invoke(
        app,
        [
            "--root",
            str(explicit_root),
            "--instance",
            "alpha",
            "init",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (explicit_root / "runtime_manifest.json").is_file()
    assert not (tmp_path / ".rexecop" / "instances" / "alpha").exists()
    manifest = json.loads((explicit_root / "runtime_manifest.json").read_text())
    assert manifest["runtime_instance"] == "alpha"
