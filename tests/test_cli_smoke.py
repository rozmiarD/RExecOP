import json
from pathlib import Path

from typer.testing import CliRunner

from rexecop import __version__
from rexecop.cli import app

runner = CliRunner()


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "rexecop" in result.stdout.lower()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_cli_init_creates_runtime_layout_without_secrets(tmp_path: Path) -> None:
    root = tmp_path / "runtime-root"

    result = runner.invoke(app, ["--root", str(root), "init"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "initialized"
    assert payload["root"] == str(root)
    assert payload["secrets_created"] is False
    for relative in (
        "operations",
        "plans",
        "evidence",
        "receipts",
        "sclite",
        "approvals",
        "queue",
        "locks",
        "inbox",
        "dead_letter",
        "triggers",
        "watchdog",
        "watchdog/records",
        "watchdog/sclite",
    ):
        path = root / relative
        assert path.is_dir()
        assert path.stat().st_mode & 0o777 == 0o700
    assert json.loads((root / "queue" / "run_now.json").read_text()) == {"pending": []}
    assert (root / "queue" / "run_now.json").stat().st_mode & 0o777 == 0o600
    manifest = json.loads((root / "runtime_manifest.json").read_text())
    assert manifest["schema"] == "rexecop.runtime_init.v0.1"
    assert manifest["secrets_created"] is False
    assert not (root / "secrets.yaml").exists()

    second = runner.invoke(app, ["--root", str(root), "init"])

    assert second.exit_code == 0, second.output
    second_payload = json.loads(second.stdout)
    assert "operations" in second_payload["existing"]


def test_cli_init_uses_rexecop_root_envvar(tmp_path: Path) -> None:
    root = tmp_path / "env-root"

    result = runner.invoke(app, ["init"], env={"REXECOP_ROOT": str(root)})

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["root"] == str(root)
    assert (root / "runtime_manifest.json").is_file()


def test_cli_watchdog_manual_record(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "watchdog",
            "manual-record",
            "--action",
            "mark_stale",
            "--reason",
            "operator_break_glass",
            "--actor-ref",
            "operator:local-admin",
            "--scope",
            "operation:op-1",
            "--operation",
            "op-1",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "mark_stale"
    artifacts = list(Path(".rexecop/watchdog/sclite").glob("*.json"))
    assert len(artifacts) == 1
    artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert artifact["manual_recovery"]["actor_ref"] == "operator:local-admin"
    assert artifact["admission"]["allowed"] is True
