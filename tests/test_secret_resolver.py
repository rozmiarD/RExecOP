from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rexecop.environment.sanitize import sanitize_connectors_for_storage, validate_no_inline_secrets
from rexecop.errors import RExecOpValidationError
from rexecop.secrets.resolver import ChainedSecretResolver, EnvSecretResolver, FileSecretResolver


def test_env_secret_resolver_reads_rexecop_secret_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REXECOP_SECRET_PROXMOX_API_TOKEN", "token-value")
    assert EnvSecretResolver().resolve("proxmox_api_token") == "token-value"


def test_file_secret_resolver_reads_yaml(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.yaml"
    secrets_file.write_text(yaml.safe_dump({"secrets": {"pbs_api_token": "pbs-secret"}}))
    resolver = FileSecretResolver(secrets_file)
    assert resolver.resolve("pbs_api_token") == "pbs-secret"


def test_chained_resolver_falls_back_to_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REXECOP_SECRET_PBS_API_TOKEN", raising=False)
    secrets_file = tmp_path / "secrets.yaml"
    secrets_file.write_text(yaml.safe_dump({"secrets": {"pbs_api_token": "from-file"}}))
    resolver = ChainedSecretResolver(EnvSecretResolver(), FileSecretResolver(secrets_file))
    assert resolver.resolve("pbs_api_token") == "from-file"


def test_inline_secret_in_connector_config_rejected() -> None:
    with pytest.raises(RExecOpValidationError):
        validate_no_inline_secrets(
            {
                "proxmox": {
                    "enabled": True,
                    "backend": "http_api",
                    "auth": {"api_key": "plaintext"},
                }
            }
        )


def test_secret_ref_fields_allowed_in_connector_config() -> None:
    sanitized = sanitize_connectors_for_storage(
        {
            "proxmox": {
                "enabled": True,
                "backend": "http_api",
                "auth": {"secret_ref": "proxmox_api_token"},
            }
        }
    )
    assert sanitized["proxmox"]["auth"]["secret_ref"] == "proxmox_api_token"
