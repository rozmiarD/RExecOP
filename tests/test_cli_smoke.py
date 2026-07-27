import json
from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from rexecop import __version__
from rexecop.cli import app

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]


class _DiagnosticEntryPoint:
    def __init__(self, name: str, loaded, *, distribution: object | None = None) -> None:
        self.name = name
        self._loaded = loaded
        self.dist = distribution

    def load(self):
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


def _diagnostic_entry_points(*points: _DiagnosticEntryPoint):
    def collect(**_kwargs):
        return list(points)

    return collect


def _write_doctor_fixture(root: Path) -> tuple[Path, Path, Path]:
    profile = root / "profile"
    (profile / "intents").mkdir(parents=True)
    (profile / "workflows").mkdir()
    (profile / "connectors").mkdir()
    (profile / "validation_rules").mkdir()
    (profile / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_contract": {
                    "name": "doctor_fixture",
                    "version": "0.1",
                    "intents": {"required": True},
                    "workflows": {"required": True},
                    "connector_requirements": {"required": True},
                    "risk_classes": {"required": True},
                    "evidence_requirements": {"required": True},
                    "governance_expectations": {"required": True},
                    "validation_rules": {"required": True},
                    "escalation_rules": {"required": True},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (profile / "intents" / "inspect.yaml").write_text(
        yaml.safe_dump(
            {
                "intent": {
                    "id": "inspect",
                    "workflow": "workflows/inspect.yaml",
                    "risk": "low",
                    "enforce_declared_modes": True,
                    "modes": ["read_only", "dry_run"],
                    "catalog": {
                        "title": "Inspect",
                        "summary": "Inspect a doctor fixture target.",
                        "target_kinds": ["fixture"],
                        "required_capabilities": ["fixture_readonly"],
                        "side_effect_class": "none",
                        "validation_ref": "validation_rules/inspect.yaml",
                        "runbook_ref": "docs/inspect.md",
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (profile / "workflows" / "inspect.yaml").write_text(
        yaml.safe_dump(
            {
                "workflow": {
                    "id": "doctor.inspect",
                    "intent": "inspect",
                    "mode": "read_only",
                    "risk": "low",
                    "description": "Doctor fixture workflow.",
                    "steps": [
                        {
                            "id": "read",
                            "type": "connector",
                            "connector": "fixture",
                            "action": "read",
                            "pause_safe": True,
                        }
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (profile / "connectors" / "fixture.yaml").write_text(
        yaml.safe_dump(
            {
                "connector": {
                    "name": "fixture",
                    "backend": "static_fixture",
                    "capabilities": ["read"],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (profile / "validation_rules" / "inspect.yaml").write_text(
        "validation_rules: []\n",
        encoding="utf-8",
    )
    environment = root / "env.yaml"
    environment.write_text(
        yaml.safe_dump(
            {
                "environment": {
                    "id": "doctor-env",
                    "profile": "doctor_fixture",
                    "targets": {"fixture-target": {"type": "fixture"}},
                    "connectors": {
                        "fixture": {
                            "enabled": True,
                            "backend": "static_fixture",
                            "fixture_only": True,
                            "actions": {"read": {"data": {"ok": True}}},
                        }
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    catalog = root / "targets.yaml"
    catalog.write_text(
        yaml.safe_dump(
            {
                "target_catalog": {
                    "version": "0.1",
                    "targets": [
                        {
                            "id": "fixture-target",
                            "target_kind": "fixture",
                            "profile_ref": str(profile),
                            "environment_ref": str(environment),
                            "environment_target": "fixture-target",
                            "capabilities": ["fixture_readonly"],
                            "connector_refs": ["fixture"],
                            "classification": {"criticality": "low"},
                        }
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return profile / "profile.yaml", environment, catalog


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


def test_cli_init_guided_returns_next_steps(tmp_path: Path) -> None:
    root = tmp_path / "runtime-root"

    result = runner.invoke(app, ["--root", str(root), "init", "--guided"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["guided"] is True
    assert payload["secrets_created"] is False
    assert any("doctor" in item for item in payload["next_steps"])


def test_cli_doctor_reports_missing_runtime_root_blocker(tmp_path: Path) -> None:
    root = tmp_path / "missing-root"

    result = runner.invoke(app, ["--root", str(root), "doctor"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocker"
    assert "runtime_root" in payload["blockers"]
    assert "runtime_root_compatibility" in payload["blockers"]
    compatibility = next(
        check for check in payload["checks"] if check["id"] == "runtime_root_compatibility"
    )
    assert compatibility["details"]["reason_code"] == "runtime_root_manifest_missing"
    assert f"rexecop --root {root} init" in payload["next_actions"]


def test_cli_queue_rejects_missing_manifest_without_creating_root(tmp_path: Path) -> None:
    root = tmp_path / "missing-root"

    result = runner.invoke(app, ["--root", str(root), "queue"])

    assert result.exit_code == 1
    assert "error: runtime_root_manifest_missing" in result.output
    assert "Traceback" not in result.output
    assert not root.exists()


def test_cli_doctor_reports_malformed_manifest_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "malformed-root"
    root.mkdir()
    manifest_path = root / "runtime_manifest.json"
    manifest_path.write_bytes(b"{not-json\n")
    before = {path.name for path in root.iterdir()}

    result = runner.invoke(app, ["--root", str(root), "doctor"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    compatibility = next(
        check for check in payload["checks"] if check["id"] == "runtime_root_compatibility"
    )
    assert compatibility["status"] == "blocker"
    assert compatibility["details"]["reason_code"] == "runtime_root_manifest_invalid"
    assert compatibility["next_action"] == (
        "use a verified backup or initialize a new runtime root"
    )
    assert manifest_path.read_bytes() == b"{not-json\n"
    assert {path.name for path in root.iterdir()} == before


def test_cli_doctor_reports_configured_backend_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REXECOP_STORAGE", "file")
    root = tmp_path / "file-root"
    assert runner.invoke(app, ["--root", str(root), "init"]).exit_code == 0

    result = runner.invoke(
        app,
        ["--root", str(root), "--storage", "sqlite", "doctor"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    compatibility = next(
        check for check in payload["checks"] if check["id"] == "runtime_root_compatibility"
    )
    assert compatibility["status"] == "blocker"
    assert compatibility["details"]["reason_code"] == (
        "runtime_root_storage_backend_mismatch"
    )
    assert compatibility["details"]["stored_storage_backend"] == "file"
    assert compatibility["details"]["configured_storage_backend"] == "sqlite"


def test_cli_doctor_warns_without_operator_inputs_after_init(tmp_path: Path) -> None:
    root = tmp_path / "runtime-root"
    assert runner.invoke(app, ["--root", str(root), "init"]).exit_code == 0

    result = runner.invoke(app, ["--root", str(root), "doctor"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "warning"
    assert "environment" in payload["warnings"]
    assert "catalog" in payload["warnings"]


def test_cli_doctor_passes_with_fixture_profile_env_and_catalog(tmp_path: Path) -> None:
    root = tmp_path / "runtime-root"
    profile, environment, catalog = _write_doctor_fixture(tmp_path)
    assert runner.invoke(app, ["--root", str(root), "init"]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "--root",
            str(root),
            "doctor",
            "--profile",
            str(profile),
            "--env",
            str(environment),
            "--catalog",
            str(catalog),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["blockers"] == []
    assert payload["warnings"] == []


def test_cli_doctor_reports_stale_profile_and_internal_plugins_without_traceback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime-root"
    assert runner.invoke(app, ["--root", str(root), "init"]).exit_code == 0
    sensitive = "doctor-plugin-payload-must-not-leak-5df1"
    sensitive_profile_path = "/private/operator/secret-profile-root"
    sensitive_distribution = "/private/operator/secret-distribution"
    hostile_exception = type("HostileDoctorFailure", (RuntimeError,), {})
    hostile_exception.__name__ = "Failure/private/module.target"
    profile_point = _DiagnosticEntryPoint(
        "stale_profile",
        lambda: sensitive_profile_path,
    )
    broken_point = _DiagnosticEntryPoint(
        "broken\n" + ("E" * 120),
        hostile_exception(f"{sensitive} module.target"),
        distribution=type("Distribution", (), {"name": sensitive_distribution})(),
    )
    collision_point = _DiagnosticEntryPoint(
        "collision_actions",
        lambda: {"record_execution_checkpoint": lambda _context: {}},
    )
    with (
        patch(
            "rexecop.profile.resolver.entry_points",
            side_effect=_diagnostic_entry_points(profile_point),
        ),
        patch(
            "rexecop.execution.internal_registry.entry_points",
            side_effect=_diagnostic_entry_points(broken_point, collision_point),
        ),
        patch("rexecop.connectors.fixture_loader.entry_points", return_value=[]),
    ):
        result = runner.invoke(
            app,
            ["--root", str(root), "doctor", "--profile", "stale_profile"],
        )

    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    assert sensitive not in result.output
    assert sensitive_profile_path not in result.output
    assert sensitive_distribution not in result.output
    assert "Failure/private/module.target" not in result.output
    assert "module.target" not in result.output
    payload = json.loads(result.stdout)
    checks = {check["id"]: check for check in payload["checks"]}
    assert {
        "plugin_posture",
        "profile_conformance",
        "stack_packages",
        "environment",
        "catalog",
    }.issubset(checks)
    plugin = checks["plugin_posture"]
    assert plugin["status"] == "blocker"
    assert plugin["details"]["reason_code"] == "plugin_incompatible"
    incompatible = plugin["details"]["incompatible_plugins"]
    assert {item["reason_codes"][0] for item in incompatible} == {
        "entry_point_load_failed",
        "action_collision",
    }
    assert all(len(item["name"]) <= 96 for item in incompatible)
    assert all("\n" not in item["name"] for item in incompatible)
    broken = next(
        item for item in incompatible if item["reason_codes"] == ["entry_point_load_failed"]
    )
    assert broken["name"].startswith("unknown~")
    assert broken["distribution"].startswith("unknown~")
    assert broken["exception_class"].startswith("Exception~")
    collision = next(item for item in incompatible if item["name"] == "collision_actions")
    assert collision["conflicting_actions"] == ["record_execution_checkpoint"]
    assert len(collision["conflicting_actions"]) <= 64
    assert checks["profile_conformance"]["status"] == "blocker"
    assert "profile_path_not_directory:NotADirectoryError" in checks[
        "profile_conformance"
    ]["summary"]
    assert "repair or remove" in plugin["next_action"]
    assert "python -m pip check" in plugin["next_action"]


def test_cli_doctor_reports_profile_enumeration_failure_as_json_and_continues(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime-root"
    assert runner.invoke(app, ["--root", str(root), "init"]).exit_code == 0
    sensitive = "profile-enumeration-private-sentinel-18d7"
    with (
        patch(
            "rexecop.profile.resolver.entry_points",
            side_effect=RuntimeError(sensitive),
        ),
        patch("rexecop.execution.internal_registry.entry_points", return_value=[]),
        patch("rexecop.connectors.fixture_loader.entry_points", return_value=[]),
    ):
        result = runner.invoke(
            app,
            ["--root", str(root), "doctor", "--profile", "stale_profile"],
        )

    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    assert sensitive not in result.output
    payload = json.loads(result.stdout)
    checks = {check["id"]: check for check in payload["checks"]}
    profile = checks["profile_conformance"]
    assert profile["status"] == "blocker"
    assert profile["details"] == {
        "profile": "stale_profile",
        "reason_code": "entry_point_enumeration_failed",
        "exception_class": "RuntimeError",
    }
    assert {"plugin_posture", "stack_packages", "environment", "catalog"}.issubset(checks)


def test_cli_env_lint_passes_with_fixture_environment(tmp_path: Path) -> None:
    profile, environment, _catalog = _write_doctor_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "env",
            "lint",
            "--env",
            str(environment),
            "--profile",
            str(profile),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["environment"]["id"] == "doctor-env"
    assert payload["environment"]["secret_ref_count"] == 0


def test_cli_profile_lint_passes_readonly_fixture_track(tmp_path: Path) -> None:
    profile, _environment, _catalog = _write_doctor_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "profile",
            "lint",
            "--profile",
            str(profile),
            "--track",
            "readonly",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["track"] == "readonly"
    assert payload["checked_intents"] == ["inspect"]


def test_cli_watchdog_manual_record(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0

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
