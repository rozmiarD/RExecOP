from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from rexecop.catalog.loader import load_catalog_document
from rexecop.cli import app
from rexecop.environment.loader import load_environment
from rexecop.errors import RExecOpValidationError
from rexecop.secrets.doctor import REDACTION_PROBE, run_secrets_doctor
from rexecop.secrets.suggest import SECRETS_SUGGEST_REF_SCHEMA, suggest_secret_refs

ROOT = Path(__file__).resolve().parents[1]
STAGING_ENV = ROOT / "examples/environments/runtime-fixture.staging.example.yaml"
FIRST_RUN_PROFILE = ROOT / "examples/first-run-demo/profile/profile.yaml"

runner = CliRunner()


def _write_secrets_file(path: Path, values: dict[str, str]) -> None:
    path.write_text(yaml.safe_dump({"secrets": values}))
    path.chmod(0o600)


def _write_environment(
    path: Path,
    *,
    env_id: str,
    refs: tuple[str, ...],
    targets: tuple[str, ...] = ("fixture-target",),
    connector_names: tuple[str, ...] = ("fixture",),
    backend: str = "static_fixture",
) -> None:
    connectors: dict[str, dict[str, object]] = {}
    for name in connector_names:
        connector: dict[str, object] = {
            "enabled": True,
            "backend": backend,
            "fixture_only": True,
            "actions": {"read": {"data": {"ok": True}}},
        }
        if refs:
            connector["base_url_secret_ref"] = refs[0]
        if len(refs) > 1:
            connector["auth"] = {"secret_ref": refs[1]}
        connectors[name] = connector
    path.write_text(
        yaml.safe_dump(
            {
                "environment": {
                    "id": env_id,
                    "profile": "first_run_demo",
                    "description": "private-environment-marker",
                    "targets": {target: {"type": "fixture"} for target in targets},
                    "connectors": connectors,
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_catalog(
    path: Path,
    references: tuple[tuple[Path, str], ...],
) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "target_catalog": {
                    "version": "0.1",
                    "targets": [
                        {
                            "id": target,
                            "target_kind": "fixture",
                            "profile_ref": str(FIRST_RUN_PROFILE),
                            "environment_ref": str(environment),
                            "environment_target": target,
                            "capabilities": ["fixture_readonly"],
                            "connector_refs": ["fixture"],
                            "classification": {"criticality": "low"},
                        }
                        for environment, target in references
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_secrets_doctor_passes_with_env_backed_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REXECOP_SECRET_FIXTURE_API_TOKEN", "hidden-token-value")
    monkeypatch.setenv("REXECOP_SECRET_FIXTURE_BASE_URL", "https://fixture.example")

    result = run_secrets_doctor(env_path=STAGING_ENV)

    assert result["status"] == "passed"
    assert result["blockers"] == []
    assert "missing_refs" not in result["blockers"]
    rendered = json.dumps(result, sort_keys=True)
    assert "hidden-token-value" not in rendered
    assert REDACTION_PROBE not in rendered


def test_secrets_doctor_reports_missing_refs(tmp_path: Path) -> None:
    env_path = tmp_path / "env.yaml"
    env_path.write_text(
        yaml.safe_dump(
            {
                "environment": {
                    "id": "secrets-doctor-missing",
                    "profile": "runtime_fixture",
                    "targets": {"host": {"type": "fixture", "criticality": "low"}},
                    "connectors": {
                        "fixture_source": {
                            "enabled": True,
                            "backend": "http_api",
                            "auth": {"secret_ref": "missing_token"},
                        }
                    },
                }
            }
        )
    )

    result = run_secrets_doctor(env_path=env_path)

    assert result["status"] == "blocker"
    assert "missing_refs" in result["blockers"]
    missing = next(
        check for check in result["checks"] if check["id"] == "missing_refs"
    )
    assert missing["details"]["missing"][0]["ref"] == "missing_token"


def test_secrets_doctor_reports_duplicate_ref_usage(tmp_path: Path) -> None:
    env_path = tmp_path / "env.yaml"
    env_path.write_text(
        yaml.safe_dump(
            {
                "environment": {
                    "id": "secrets-doctor-duplicate",
                    "profile": "runtime_fixture",
                    "targets": {"host": {"type": "fixture", "criticality": "low"}},
                    "connectors": {
                        "fixture_source": {
                            "enabled": True,
                            "backend": "http_api",
                            "base_url_secret_ref": "shared_ref",
                            "auth": {"secret_ref": "shared_ref"},
                        }
                    },
                }
            }
        )
    )
    secrets_file = tmp_path / "secrets.yaml"
    _write_secrets_file(secrets_file, {"shared_ref": "shared-value"})

    result = run_secrets_doctor(env_path=env_path, secrets_file=secrets_file)

    assert result["status"] == "warning"
    assert "duplicate_refs" in result["warnings"]
    assert "shared-value" not in json.dumps(result)


def test_secrets_doctor_reports_secrets_file_permission_blocker(tmp_path: Path) -> None:
    env_path = tmp_path / "env.yaml"
    env_path.write_text(
        yaml.safe_dump(
            {
                "environment": {
                    "id": "secrets-doctor-perms",
                    "profile": "runtime_fixture",
                    "targets": {"host": {"type": "fixture", "criticality": "low"}},
                    "connectors": {
                        "fixture_source": {
                            "enabled": True,
                            "backend": "http_api",
                            "auth": {"secret_ref": "fixture_api_token"},
                        }
                    },
                }
            }
        )
    )
    secrets_file = tmp_path / "secrets.yaml"
    _write_secrets_file(secrets_file, {"fixture_api_token": "hidden"})
    secrets_file.chmod(0o640)

    result = run_secrets_doctor(env_path=env_path, secrets_file=secrets_file)

    assert result["status"] == "blocker"
    assert "secrets_file_permissions" in result["blockers"]
    assert "hidden" not in json.dumps(result)


def test_secrets_doctor_redaction_self_test_passes() -> None:
    result = run_secrets_doctor(env_path=STAGING_ENV)
    redaction = next(
        check for check in result["checks"] if check["id"] == "redaction_self_test"
    )
    assert redaction["status"] == "passed"


def test_cli_secrets_doctor_requires_input() -> None:
    result = runner.invoke(app, ["secrets", "doctor"])
    assert result.exit_code == 1
    assert "provide --env and/or --catalog" in result.stderr


@pytest.mark.parametrize("command", [("doctor",), ("suggest-ref",)])
def test_secret_cli_invalid_yaml_uses_stable_redacted_error(
    tmp_path: Path,
    command: tuple[str, ...],
) -> None:
    env_path = tmp_path / "private-environment.yaml"
    env_path.write_text(
        "environment:\n  id: first\n  id: private-id-marker\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["--json", "secrets", *command, "--env", str(env_path)],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["reason_code"] == "invalid_yaml_structure"
    assert payload["message"] == "YAML input is invalid or exceeds structural limits"
    assert "private-id-marker" not in result.stdout
    assert str(env_path) not in result.stdout


def test_secrets_doctor_cli_invalid_utf8_file_stays_bounded(
    tmp_path: Path,
) -> None:
    secrets_file = tmp_path / "private-invalid-utf8-secrets.yaml"
    secrets_file.write_bytes(
        b"secrets:\n  fixture_ref: private-secret-value\xff\n"
    )
    secrets_file.chmod(0o600)

    result = runner.invoke(
        app,
        [
            "--json",
            "secrets",
            "doctor",
            "--env",
            str(STAGING_ENV),
            "--secrets-file",
            str(secrets_file),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["schema"] == "rexecop.secrets_doctor.v0.1"
    assert payload["status"] == "blocker"
    rendered = result.stdout
    for marker in (
        str(secrets_file),
        "private-secret-value",
        "utf-8",
        "UnicodeDecodeError",
        "codec",
        "0xff",
        "\\xff",
        "Traceback",
    ):
        assert marker not in rendered


def test_cli_secrets_doctor_passes_with_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REXECOP_SECRET_FIXTURE_API_TOKEN", "hidden-token-value")
    monkeypatch.setenv("REXECOP_SECRET_FIXTURE_BASE_URL", "https://fixture.example")

    result = runner.invoke(app, ["secrets", "doctor", "--env", str(STAGING_ENV)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema"] == "rexecop.secrets_doctor.v0.1"
    assert payload["status"] == "passed"
    assert "hidden-token-value" not in result.stdout


def test_secrets_suggest_ref_returns_names_without_reading_values(tmp_path: Path) -> None:
    env_path = tmp_path / "env.yaml"
    env_path.write_text(
        yaml.safe_dump(
            {
                "environment": {
                    "id": "suggest-ref",
                    "profile": "runtime_fixture",
                    "targets": {"host": {"type": "host"}},
                    "connectors": {
                        "api": {"enabled": True, "backend": "http_api", "actions": {}},
                        "ssh": {"enabled": True, "backend": "ssh_readonly", "allowlist": []},
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    payload = suggest_secret_refs(env_path=env_path)

    assert payload["schema"] == SECRETS_SUGGEST_REF_SCHEMA
    refs = {item["suggested_ref"] for item in payload["suggestions"]}
    assert refs == {"api_api_token", "api_base_url", "ssh_identity_file"}
    rendered = json.dumps(payload, sort_keys=True)
    assert "hidden" not in rendered
    assert "hidden-token-value" not in rendered
    assert "Does not read REXECOP_SECRETS_FILE." in payload["non_claims"]


def test_cli_secrets_suggest_ref_filters_connector(tmp_path: Path) -> None:
    env_path = tmp_path / "env.yaml"
    env_path.write_text(
        yaml.safe_dump(
            {
                "environment": {
                    "id": "suggest-ref",
                    "profile": "runtime_fixture",
                    "targets": {"host": {"type": "host"}},
                    "connectors": {
                        "api": {"enabled": True, "backend": "http_api", "actions": {}},
                        "ssh": {"enabled": True, "backend": "ssh_readonly", "allowlist": []},
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["secrets", "suggest-ref", "--env", str(env_path), "--connector", "ssh"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [item["suggested_ref"] for item in payload["suggestions"]] == [
        "ssh_identity_file"
    ]


@pytest.mark.parametrize(
    ("refs", "env_key"),
    [
        (("foo-bar", "foo_bar"), "REXECOP_SECRET_FOO_BAR"),
        (("Foo", "foo"), "REXECOP_SECRET_FOO"),
        (("foo--bar", "foo__bar"), "REXECOP_SECRET_FOO__BAR"),
    ],
)
@pytest.mark.parametrize("availability", ["environment", "file", "neither"])
def test_secrets_doctor_collision_precedes_missing_for_all_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    refs: tuple[str, str],
    env_key: str,
    availability: str,
) -> None:
    env_path = tmp_path / "collision-environment.yaml"
    _write_environment(env_path, env_id="collision-environment", refs=refs)
    secrets_file: Path | None = None
    if availability == "environment":
        monkeypatch.setenv(env_key, "shared-private-environment-value")
    elif availability == "file":
        secrets_file = tmp_path / "secrets.yaml"
        _write_secrets_file(
            secrets_file,
            {refs[0]: "first-private-file-value", refs[1]: "second-private-file-value"},
        )

    with pytest.raises(RExecOpValidationError, match="environment key collision"):
        load_environment(env_path)

    result = run_secrets_doctor(env_path=env_path, secrets_file=secrets_file)

    check_ids = [check["id"] for check in result["checks"]]
    assert check_ids.index("secret_ref_env_collision") < check_ids.index("missing_refs")
    assert "secret_ref_env_collision" in result["blockers"]
    collision = next(
        check for check in result["checks"] if check["id"] == "secret_ref_env_collision"
    )
    assert collision["details"]["collisions"][0]["refs"] == sorted(refs)
    rendered = json.dumps(result, sort_keys=True)
    assert "shared-private-environment-value" not in rendered
    assert "first-private-file-value" not in rendered
    assert "second-private-file-value" not in rendered
    assert "private-environment-marker" not in rendered


def test_doctor_aggregates_catalog_environments_and_deduplicates_physical_document(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit.yaml"
    referenced = tmp_path / "referenced.yaml"
    catalog = tmp_path / "catalog.yaml"
    secrets_file = tmp_path / "secrets.yaml"
    _write_environment(explicit, env_id="explicit", refs=("foo-bar",))
    _write_environment(
        referenced,
        env_id="referenced",
        refs=("foo_bar",),
        targets=("fixture-target", "fixture-target-2"),
    )
    _write_catalog(
        catalog,
        (
            (referenced, "fixture-target"),
            (referenced, "fixture-target-2"),
        ),
    )
    _write_secrets_file(
        secrets_file,
        {"foo-bar": "first-file-value", "foo_bar": "second-file-value"},
    )

    result = run_secrets_doctor(
        env_path=explicit,
        catalog_path=catalog,
        secrets_file=secrets_file,
    )

    collision = next(
        check for check in result["checks"] if check["id"] == "secret_ref_env_collision"
    )
    group = collision["details"]["collisions"][0]
    assert group["binding_count"] == 2
    assert len(group["paths"]) == 2
    assert result["summary"]["documents_checked"] == [
        "environment",
        "catalog_environment[0]",
    ]


def test_same_exact_ref_across_documents_is_duplicate_warning_not_collision(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit.yaml"
    referenced = tmp_path / "referenced.yaml"
    catalog = tmp_path / "catalog.yaml"
    secrets_file = tmp_path / "secrets.yaml"
    _write_environment(explicit, env_id="explicit", refs=("shared-ref",))
    _write_environment(referenced, env_id="referenced", refs=("shared-ref",))
    _write_catalog(catalog, ((referenced, "fixture-target"),))
    _write_secrets_file(secrets_file, {"shared-ref": "shared-file-value"})

    result = run_secrets_doctor(
        env_path=explicit,
        catalog_path=catalog,
        secrets_file=secrets_file,
    )

    collision = next(
        check for check in result["checks"] if check["id"] == "secret_ref_env_collision"
    )
    assert collision["status"] == "passed"
    assert "secret_ref_env_collision" not in result["blockers"]
    assert "duplicate_refs" in result["warnings"]
    assert "shared-file-value" not in json.dumps(result, sort_keys=True)


def test_normal_environment_keeps_hyphenated_exact_ref_reuse_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = tmp_path / "hyphenated.yaml"
    _write_environment(
        environment,
        env_id="hyphenated",
        refs=("foo-bar", "foo-bar"),
    )
    monkeypatch.setenv("REXECOP_SECRET_FOO_BAR", "hyphen-private-value")

    loaded = load_environment(environment)
    result = run_secrets_doctor(env_path=environment)

    assert loaded.connectors["fixture"]["base_url_secret_ref"] == "foo-bar"
    assert "secret_ref_env_collision" not in result["blockers"]
    assert "duplicate_refs" in result["warnings"]
    assert "hyphen-private-value" not in json.dumps(result, sort_keys=True)


def test_normal_catalog_load_fails_closed_for_referenced_environment_collision(
    tmp_path: Path,
) -> None:
    environment = tmp_path / "collision.yaml"
    catalog = tmp_path / "catalog.yaml"
    _write_environment(
        environment,
        env_id="catalog-collision",
        refs=("foo-bar", "foo_bar"),
    )
    _write_catalog(catalog, ((environment, "fixture-target"),))

    with pytest.raises(RExecOpValidationError, match="environment key collision"):
        load_catalog_document(catalog)


def test_normal_catalog_load_fails_closed_for_collision_across_environments(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    catalog = tmp_path / "catalog.yaml"
    _write_environment(first, env_id="first", refs=("foo-bar",))
    _write_environment(
        second,
        env_id="second",
        refs=("foo_bar",),
        targets=("fixture-target-2",),
    )
    _write_catalog(
        catalog,
        ((first, "fixture-target"), (second, "fixture-target-2")),
    )

    with pytest.raises(RExecOpValidationError, match="environment key collision"):
        load_catalog_document(catalog)


def test_collision_doctor_cli_json_and_table_exit_one_without_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / "collision.yaml"
    _write_environment(
        env_path,
        env_id="cli-collision",
        refs=("foo-bar", "foo_bar"),
    )
    monkeypatch.setenv("REXECOP_SECRET_FOO_BAR", "cli-private-value")

    json_result = runner.invoke(app, ["secrets", "doctor", "--env", str(env_path)])
    table_result = runner.invoke(
        app,
        ["--format", "table", "secrets", "doctor", "--env", str(env_path)],
    )

    assert json_result.exit_code == 1
    payload = json.loads(json_result.stdout)
    assert payload["status"] == "blocker"
    assert "secret_ref_env_collision" in payload["blockers"]
    assert "cli-private-value" not in json_result.stdout
    assert table_result.exit_code == 1
    assert "secrets doctor status=blocker" in table_result.stdout
    assert "blockers=secret_ref_env_collision" in table_result.stdout
    assert "cli-private-value" not in table_result.stdout


def test_suggest_ref_rejects_complete_set_duplicate_ambiguity(tmp_path: Path) -> None:
    env_path = tmp_path / "suggestion-collision.yaml"
    _write_environment(
        env_path,
        env_id="suggestion-collision",
        refs=(),
        connector_names=("api-one", "api_one"),
        backend="http_api",
    )

    with pytest.raises(
        RExecOpValidationError,
        match="generated secret_ref suggestions are ambiguous; use explicit refs",
    ):
        suggest_secret_refs(env_path=env_path)

    cli_result = runner.invoke(
        app,
        ["secrets", "suggest-ref", "--env", str(env_path)],
    )
    assert cli_result.exit_code == 1
    assert "generated secret_ref suggestions are ambiguous" in cli_result.stderr
    assert "private-environment-marker" not in cli_result.stderr


@pytest.mark.parametrize(
    ("existing_ref", "overlaps_generated"),
    [
        ("generated_identity_file", True),
        ("generated-identity-file", True),
        ("unrelated_identity_file", False),
    ],
)
def test_suggest_ref_connector_filter_validates_all_existing_identities(
    tmp_path: Path,
    existing_ref: str,
    overlaps_generated: bool,
) -> None:
    env_path = tmp_path / "existing-generated-overlap.yaml"
    env_path.write_text(
        yaml.safe_dump(
            {
                "environment": {
                    "id": "existing-generated-overlap",
                    "profile": "first_run_demo",
                    "targets": {"fixture-target": {"type": "fixture"}},
                    "connectors": {
                        "source": {
                            "enabled": True,
                            "backend": "http_api",
                            "base_url_secret_ref": existing_ref,
                            "auth": {"secret_ref": "source_api_token"},
                        },
                        "generated": {
                            "enabled": True,
                            "backend": "ssh_readonly",
                            "allowlist": [],
                        },
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    if overlaps_generated:
        for connector_filter in (None, "generated"):
            with pytest.raises(
                RExecOpValidationError,
                match="generated secret_ref suggestions are ambiguous; use explicit refs",
            ):
                suggest_secret_refs(
                    env_path=env_path,
                    connector=connector_filter,
                )
        return

    payload = suggest_secret_refs(env_path=env_path, connector="generated")

    assert payload["existing_refs"] == []
    assert [item["suggested_ref"] for item in payload["suggestions"]] == [
        "generated_identity_file"
    ]
