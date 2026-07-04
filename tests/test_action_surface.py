from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from rexecop.action.configure import ACTION_CONFIGURE_SCHEMA, configure_action
from rexecop.action.surface import (
    ACTION_LIST_SCHEMA,
    ACTION_PREVIEW_SCHEMA,
    ACTION_SHOW_SCHEMA,
    ACTION_VALIDATE_SCHEMA,
    list_actions,
    preview_action,
    show_action,
    validate_actions,
)
from rexecop.cli import app

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "examples/first-run-demo/profile/profile.yaml"
ENVIRONMENT = ROOT / "examples/first-run-demo/environment.yaml"
CATALOG = ROOT / "examples/first-run-demo/catalog.yaml"
runner = CliRunner()


def test_action_list_reports_profile_env_actions_without_backend_io() -> None:
    with patch("rexecop.connectors.http_api.urllib.request.urlopen") as backend:
        payload = list_actions(profile=PROFILE, env=ENVIRONMENT)

    backend.assert_not_called()
    assert payload["schema"] == ACTION_LIST_SCHEMA
    actions = {item["id"]: item for item in payload["actions"]}
    assert set(actions) == {"inspect"}
    assert actions["inspect"]["backend_classes"] == ["static_fixture"]
    assert actions["inspect"]["side_effect_class"] == "none"
    assert actions["inspect"]["operation_descriptor_digest"]
    assert payload["non_claims"]


def test_action_show_redacts_config_and_reports_contract_sources() -> None:
    payload = show_action("inspect", profile=PROFILE, env=ENVIRONMENT)

    assert payload["schema"] == ACTION_SHOW_SCHEMA
    assert payload["action"]["id"] == "inspect"
    assert payload["source_contracts"]["profile_digest"]
    assert payload["source_contracts"]["environment_digest"]
    assert payload["workflow"]["connector_steps"] == [
        {
            "id": "read",
            "connector": "fixture",
            "action": "read",
            "backend_class": "static_fixture",
            "enabled": True,
            "shape_digest": "",
            "contract_declared": True,
            "environment_configured": True,
        }
    ]
    rendered = json.dumps(payload, sort_keys=True)
    assert "secret_ref" not in rendered
    assert "first-run-demo" not in rendered
    assert "Does not request or imply GovEngine admission." in payload["non_claims"]


def test_action_validate_all_passes_without_backend_io() -> None:
    with patch("rexecop.connectors.http_api.urllib.request.urlopen") as backend:
        payload = validate_actions(profile=PROFILE, env=ENVIRONMENT)

    backend.assert_not_called()
    assert payload["schema"] == ACTION_VALIDATE_SCHEMA
    assert payload["status"] == "passed"
    assert payload["actions_checked"] == ["inspect"]
    assert payload["blockers"] == []


def test_action_preview_redacts_http_effective_call_without_backend_io(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    (profile / "connectors").mkdir(parents=True)
    (profile / "intents").mkdir()
    (profile / "workflows").mkdir()
    (profile / "validation_rules").mkdir()
    (profile / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_contract": {
                    "name": "http_preview",
                    "version": "0.1.0",
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
    (profile / "connectors" / "api.yaml").write_text(
        yaml.safe_dump(
            {
                "connector": {
                    "name": "api",
                    "backend": "http_api",
                    "capabilities": ["read_state"],
                    "action_shapes": {
                        "read_state": {
                            "method": "GET",
                            "path": "/fixture/{target}/state",
                            "unwrap": "state",
                            "mutating": False,
                            "max_response_bytes": 2048,
                        }
                    },
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
                    "modes": ["dry_run"],
                    "catalog": {
                        "title": "Inspect",
                        "summary": "Inspect HTTP preview target.",
                        "target_kinds": ["fixture"],
                        "required_capabilities": ["readonly_api"],
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
                    "id": "http_preview.inspect",
                    "intent": "inspect",
                    "mode": "read_only",
                    "risk": "low",
                    "steps": [
                        {
                            "id": "read",
                            "type": "connector",
                            "connector": "api",
                            "action": "read_state",
                        }
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (profile / "validation_rules" / "inspect.yaml").write_text("rules: []\n", encoding="utf-8")
    env_path = tmp_path / "env.yaml"
    env_path.write_text(
        yaml.safe_dump(
            {
                "environment": {
                    "id": "http-preview",
                    "profile": "http_preview",
                    "targets": {"fixture-target": {"type": "fixture"}},
                    "connectors": {
                        "api": {
                            "enabled": True,
                            "backend": "http_api",
                            "base_url_secret_ref": "fixture_base_url",
                            "auth": {
                                "secret_ref": "fixture_api_token",
                                "header": "Authorization",
                                "prefix": "Bearer ",
                            },
                            "actions": {
                                "read_state": {
                                    "method": "GET",
                                    "path": "/fixture/{target}/state",
                                    "unwrap": "state",
                                    "max_response_bytes": 2048,
                                }
                            },
                        }
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with patch("rexecop.connectors.http_api.urllib.request.urlopen") as backend:
        payload = preview_action(
            "inspect",
            profile=profile / "profile.yaml",
            env=env_path,
            target="fixture-target",
        )

    backend.assert_not_called()
    assert payload["schema"] == ACTION_PREVIEW_SCHEMA
    preview = payload["previews"][0]["call_preview"]
    assert preview["kind"] == "http_api"
    assert preview["method"] == "GET"
    assert preview["path_preview"] == "/fixture/fixture-target/state"
    assert preview["headers"]["auth_configured"] is True
    assert preview["bounded_output"]["max_response_bytes"] == 2048
    rendered = json.dumps(payload, sort_keys=True)
    assert "fixture_base_url" not in rendered
    assert "fixture_api_token" not in rendered
    assert "Bearer" not in rendered
    assert "Does not execute backend IO." in payload["non_claims"]


def test_action_preview_redacts_static_fixture_data_without_backend_io() -> None:
    payload = preview_action("inspect", profile=PROFILE, env=ENVIRONMENT)

    assert payload["schema"] == ACTION_PREVIEW_SCHEMA
    preview = payload["previews"][0]["call_preview"]
    assert preview["kind"] == "static_fixture"
    assert preview["data_digest"].startswith("sha256:")
    rendered = json.dumps(payload, sort_keys=True)
    assert "ready" not in rendered
    assert "observed" not in rendered


def test_action_preview_renders_readonly_command_shapes_without_endpoint_data(
    tmp_path: Path,
) -> None:
    for backend in ("local_shell_readonly", "ssh_readonly"):
        profile, env_path = _write_command_preview_fixture(tmp_path / backend, backend)

        payload = preview_action("inspect", profile=profile, env=env_path)

        preview = payload["previews"][0]["call_preview"]
        assert preview["kind"] == backend
        assert preview["command"]["argv"] == ["uptime", "-p"]
        assert preview["command"]["argv_digest"].startswith("sha256:")
        assert preview["bounded_output"]["max_output_bytes"] == 4096
        rendered = json.dumps(payload, sort_keys=True)
        assert "private-host" not in rendered
        assert "operator-user" not in rendered
        assert "identity_key" not in rendered


def test_action_configure_dry_run_generates_bounded_patch_without_mutating_env(
    tmp_path: Path,
) -> None:
    profile, env_path = _write_http_configure_fixture(tmp_path)
    before = env_path.read_text(encoding="utf-8")
    patch_path = tmp_path / "patch.json"

    payload = configure_action(
        "inspect",
        profile=profile,
        env=env_path,
        write_patch=patch_path,
    )

    assert payload["schema"] == ACTION_CONFIGURE_SCHEMA
    assert payload["status"] == "patch_available"
    assert env_path.read_text(encoding="utf-8") == before
    operation = payload["patch"]["operations"][-1]
    assert operation["op"] == "add"
    assert operation["path"] == "/environment/connectors/api/actions/read_state"
    assert operation["value"] == {
        "method": "GET",
        "path": "/state",
        "unwrap": "state",
        "max_response_bytes": 2048,
    }
    assert patch_path.exists()
    rendered = patch_path.read_text(encoding="utf-8")
    assert "rexecop.action_configure_patch.v0.1" in rendered
    assert "secret_ref" not in rendered


def test_cli_action_configure_rejects_non_dry_run(tmp_path: Path) -> None:
    profile, env_path = _write_http_configure_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "action",
            "configure",
            "inspect",
            "--profile",
            str(profile),
            "--env",
            str(env_path),
            "--no-dry-run",
        ],
    )

    assert result.exit_code == 1
    assert "only supports --dry-run" in result.stderr


def test_action_list_can_resolve_profile_and_env_from_catalog() -> None:
    payload = list_actions(catalog=CATALOG, target="fixture-target")

    assert payload["catalog"]["digest"]
    assert payload["catalog"]["target"] == "fixture-target"
    assert str(CATALOG) not in json.dumps(payload, sort_keys=True)
    assert payload["actions"][0]["applicability"]["applicable"] is True


def test_action_validate_reports_shape_drift_without_backend_io(tmp_path: Path) -> None:
    env = yaml.safe_load(ENVIRONMENT.read_text(encoding="utf-8"))
    env["environment"]["connectors"]["fixture"]["enabled"] = False
    env_path = tmp_path / "drift-env.yaml"
    env_path.write_text(yaml.safe_dump(env, sort_keys=False), encoding="utf-8")

    with patch("rexecop.connectors.http_api.urllib.request.urlopen") as backend:
        payload = validate_actions(
            profile=PROFILE,
            env=env_path,
            intent="inspect",
        )

    backend.assert_not_called()
    assert payload["status"] == "failed"
    assert "inspect:workflow_contract" in payload["blockers"]
    check = next(
        item
        for item in payload["checks"][0]["checks"]
        if item["id"] == "workflow_contract"
    )
    assert check["id"] == "workflow_contract"
    assert "connector disabled" in check["summary"]


def test_cli_action_commands_emit_json() -> None:
    commands = [
        ["action", "list", "--profile", str(PROFILE), "--env", str(ENVIRONMENT)],
        [
            "action",
            "show",
            "inspect",
            "--profile",
            str(PROFILE),
            "--env",
            str(ENVIRONMENT),
        ],
        [
            "action",
            "preview",
            "inspect",
            "--profile",
            str(PROFILE),
            "--env",
            str(ENVIRONMENT),
        ],
        [
            "action",
            "validate",
            "--all",
            "--profile",
            str(PROFILE),
            "--env",
            str(ENVIRONMENT),
        ],
    ]
    for command in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.stdout + result.stderr
        assert isinstance(json.loads(result.stdout), dict)


def test_cli_action_validate_requires_scope() -> None:
    result = runner.invoke(
        app,
        ["action", "validate", "--profile", str(PROFILE), "--env", str(ENVIRONMENT)],
    )

    assert result.exit_code == 1
    assert "requires --all or --intent" in result.stderr


def _write_command_preview_fixture(root: Path, backend: str) -> tuple[Path, Path]:
    profile = root / "profile"
    (profile / "connectors").mkdir(parents=True)
    (profile / "intents").mkdir()
    (profile / "workflows").mkdir()
    (profile / "validation_rules").mkdir()
    (profile / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_contract": {
                    "name": f"{backend}_preview",
                    "version": "0.1.0",
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
    (profile / "connectors" / "host.yaml").write_text(
        yaml.safe_dump(
            {
                "connector": {
                    "name": "host",
                    "backend": backend,
                    "capabilities": ["uptime"],
                    "command_shapes": {
                        "uptime": {
                            "command": "uptime",
                            "args": ["-p"],
                        }
                    },
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
                    "modes": ["dry_run"],
                    "catalog": {
                        "title": "Inspect",
                        "summary": "Inspect command preview target.",
                        "target_kinds": ["host"],
                        "required_capabilities": ["readonly_shell"],
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
                    "id": f"{backend}_preview.inspect",
                    "intent": "inspect",
                    "mode": "read_only",
                    "risk": "low",
                    "steps": [
                        {
                            "id": "read",
                            "type": "connector",
                            "connector": "host",
                            "action": "uptime",
                        }
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (profile / "validation_rules" / "inspect.yaml").write_text("rules: []\n", encoding="utf-8")
    config: dict[str, object] = {
        "enabled": True,
        "backend": backend,
        "allowlist": [{"action": "uptime", "command": "uptime", "args": ["-p"]}],
        "max_output_bytes": 4096,
    }
    if backend == "ssh_readonly":
        config.update(
            {
                "host": "private-host",
                "user": "operator-user",
                "identity_file_secret_ref": "identity_key",
            }
        )
    env_path = root / "env.yaml"
    env_path.write_text(
        yaml.safe_dump(
            {
                "environment": {
                    "id": f"{backend}-preview",
                    "profile": f"{backend}_preview",
                    "targets": {"host-01": {"type": "host"}},
                    "connectors": {"host": config},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return profile / "profile.yaml", env_path


def _write_http_configure_fixture(root: Path) -> tuple[Path, Path]:
    profile = root / "profile"
    (profile / "connectors").mkdir(parents=True)
    (profile / "intents").mkdir()
    (profile / "workflows").mkdir()
    (profile / "validation_rules").mkdir()
    (profile / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_contract": {
                    "name": "http_configure",
                    "version": "0.1.0",
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
    (profile / "connectors" / "api.yaml").write_text(
        yaml.safe_dump(
            {
                "connector": {
                    "name": "api",
                    "backend": "http_api",
                    "capabilities": ["read_state"],
                    "action_shapes": {
                        "read_state": {
                            "method": "GET",
                            "path": "/state",
                            "unwrap": "state",
                            "max_response_bytes": 2048,
                        }
                    },
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
                    "modes": ["dry_run"],
                    "catalog": {
                        "title": "Inspect",
                        "summary": "Inspect HTTP configure target.",
                        "target_kinds": ["fixture"],
                        "required_capabilities": ["readonly_api"],
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
                    "id": "http_configure.inspect",
                    "intent": "inspect",
                    "mode": "read_only",
                    "risk": "low",
                    "steps": [
                        {
                            "id": "read",
                            "type": "connector",
                            "connector": "api",
                            "action": "read_state",
                        }
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (profile / "validation_rules" / "inspect.yaml").write_text("rules: []\n", encoding="utf-8")
    env_path = root / "env.yaml"
    env_path.write_text(
        yaml.safe_dump(
            {
                "environment": {
                    "id": "http-configure",
                    "profile": "http_configure",
                    "targets": {"fixture-target": {"type": "fixture"}},
                    "connectors": {"api": {"enabled": True, "backend": "http_api"}},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return profile / "profile.yaml", env_path
