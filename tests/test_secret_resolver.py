from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rexecop.environment.sanitize import sanitize_connectors_for_storage, validate_no_inline_secrets
from rexecop.errors import RExecOpValidationError
from rexecop.secrets.resolver import ChainedSecretResolver, EnvSecretResolver, FileSecretResolver


def _write_secrets_file(path: Path, values: dict[str, str]) -> None:
    path.write_text(yaml.safe_dump({"secrets": values}))
    path.chmod(0o600)


def test_env_secret_resolver_reads_rexecop_secret_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REXECOP_SECRET_FIXTURE_API_TOKEN", "token-value")
    assert EnvSecretResolver().resolve("fixture_api_token") == "token-value"


def test_file_secret_resolver_reads_yaml(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.yaml"
    _write_secrets_file(secrets_file, {"fixture_api_token": "fixture-secret"})
    resolver = FileSecretResolver(secrets_file)
    assert resolver.resolve("fixture_api_token") == "fixture-secret"


def test_chained_resolver_falls_back_to_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REXECOP_SECRET_FIXTURE_API_TOKEN", raising=False)
    secrets_file = tmp_path / "secrets.yaml"
    _write_secrets_file(secrets_file, {"fixture_api_token": "from-file"})
    resolver = ChainedSecretResolver(EnvSecretResolver(), FileSecretResolver(secrets_file))
    assert resolver.resolve("fixture_api_token") == "from-file"


def test_inline_secret_in_connector_config_rejected() -> None:
    with pytest.raises(RExecOpValidationError):
        validate_no_inline_secrets(
            {
                "fixture_source": {
                    "enabled": True,
                    "backend": "http_api",
                    "auth": {"api_key": "plaintext"},
                }
            }
        )


def test_secret_ref_fields_allowed_in_connector_config() -> None:
    sanitized = sanitize_connectors_for_storage(
        {
            "fixture_source": {
                "enabled": True,
                "backend": "http_api",
                "auth": {"secret_ref": "fixture_api_token"},
            }
        }
    )
    assert sanitized["fixture_source"]["auth"]["secret_ref"] == "fixture_api_token"


def test_file_secret_resolver_rejects_group_or_world_permissions(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.yaml"
    _write_secrets_file(secrets_file, {"token": "value"})
    secrets_file.chmod(0o640)
    with pytest.raises(RExecOpValidationError, match="0600 or stricter"):
        FileSecretResolver(secrets_file).resolve("token")


def test_file_secret_resolver_rejects_symlink(tmp_path: Path) -> None:
    real_file = tmp_path / "real-secrets.yaml"
    _write_secrets_file(real_file, {"token": "value"})
    link = tmp_path / "secrets.yaml"
    link.symlink_to(real_file)
    with pytest.raises(RExecOpValidationError, match="regular file"):
        FileSecretResolver(link).resolve("token")


def test_file_secret_resolver_hides_malformed_yaml_content(tmp_path: Path) -> None:
    marker = "fixture-malformed-secret"
    secrets_file = tmp_path / "secrets.yaml"
    secrets_file.write_text(f"secrets: [token: {marker}")
    secrets_file.chmod(0o600)
    with pytest.raises(RExecOpValidationError) as raised:
        FileSecretResolver(secrets_file).resolve("token")
    assert marker not in str(raised.value)
    assert str(secrets_file) not in str(raised.value)


def test_inline_secret_outside_connectors_is_rejected() -> None:
    with pytest.raises(RExecOpValidationError, match="inline secret-like value"):
        validate_no_inline_secrets(
            {
                "environment": {
                    "targets": {"host": {"password": "plaintext"}},
                    "connectors": {},
                }
            }
        )


def test_strong_token_in_neutral_environment_field_is_rejected() -> None:
    with pytest.raises(RExecOpValidationError, match="inline secret material"):
        validate_no_inline_secrets(
            {
                "environment": {
                    "description": "github_pat_" + "A" * 60,
                    "connectors": {},
                }
            }
        )
