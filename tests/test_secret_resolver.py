from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
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


@pytest.mark.parametrize(
    ("first", "second", "env_key"),
    [
        ("foo-bar", "foo_bar", "REXECOP_SECRET_FOO_BAR"),
        ("foo_bar", "foo-bar", "REXECOP_SECRET_FOO_BAR"),
        ("Foo", "foo", "REXECOP_SECRET_FOO"),
        ("foo", "Foo", "REXECOP_SECRET_FOO"),
        ("foo--bar", "foo__bar", "REXECOP_SECRET_FOO__BAR"),
        ("foo__bar", "foo--bar", "REXECOP_SECRET_FOO__BAR"),
    ],
)
def test_env_resolver_rejects_distinct_legacy_key_collisions_in_both_orders(
    monkeypatch: pytest.MonkeyPatch,
    first: str,
    second: str,
    env_key: str,
) -> None:
    monkeypatch.setenv(env_key, "shared-secret-value")
    resolver = EnvSecretResolver()

    assert resolver.resolve(first) == "shared-secret-value"
    with pytest.raises(
        RExecOpValidationError,
        match="^secret_ref environment key collision$",
    ) as raised:
        resolver.resolve(second)

    assert "shared-secret-value" not in str(raised.value)


def test_env_resolver_allows_same_trimmed_reference_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REXECOP_SECRET_FOO_BAR", "repeat-secret-value")
    resolver = EnvSecretResolver()

    assert resolver.resolve(" foo-bar ") == "repeat-secret-value"
    assert resolver.resolve("foo-bar") == "repeat-secret-value"


def test_env_resolver_concurrent_collision_allows_at_most_one_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REXECOP_SECRET_FOO_BAR", "concurrent-secret-value")
    resolver = EnvSecretResolver()
    barrier = threading.Barrier(2)

    def resolve(ref: str) -> tuple[str, str]:
        barrier.wait()
        try:
            return "resolved", resolver.resolve(ref)
        except RExecOpValidationError as exc:
            return "failed", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(resolve, ("foo-bar", "foo_bar")))

    assert sum(status == "resolved" for status, _ in results) == 1
    assert sum(message == "secret_ref environment key collision" for _, message in results) == 1


def test_chained_collision_is_terminal_but_ordinary_missing_still_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REXECOP_SECRET_FOO_BAR", raising=False)
    monkeypatch.delenv("REXECOP_SECRET_ORDINARY_REF", raising=False)

    class SpyFileResolver:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def resolve(self, secret_ref: str) -> str:
            self.calls.append(secret_ref)
            return f"file:{secret_ref.strip()}"

    file_resolver = SpyFileResolver()
    resolver = ChainedSecretResolver(EnvSecretResolver(), file_resolver)

    assert resolver.resolve("foo-bar") == "file:foo-bar"
    with pytest.raises(RExecOpValidationError, match="environment key collision"):
        resolver.resolve("foo_bar")
    assert file_resolver.calls == ["foo-bar"]
    assert resolver.resolve("ordinary_ref") == "file:ordinary_ref"
    assert file_resolver.calls == ["foo-bar", "ordinary_ref"]


def test_chained_env_collision_never_calls_file_and_direct_file_refs_remain_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REXECOP_SECRET_FOO_BAR", "environment-secret-value")

    class SpyFileResolver:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def resolve(self, secret_ref: str) -> str:
            self.calls.append(secret_ref)
            return "unexpected-file-value"

    spy = SpyFileResolver()
    resolver = ChainedSecretResolver(EnvSecretResolver(), spy)
    assert resolver.resolve("foo-bar") == "environment-secret-value"
    with pytest.raises(RExecOpValidationError, match="environment key collision"):
        resolver.resolve("foo_bar")
    assert spy.calls == []

    secrets_file = tmp_path / "exact-secrets.yaml"
    _write_secrets_file(
        secrets_file,
        {"foo-bar": "hyphen-value", "foo_bar": "underscore-value"},
    )
    direct = FileSecretResolver(secrets_file)
    assert direct.resolve("foo-bar") == "hyphen-value"
    assert direct.resolve("foo_bar") == "underscore-value"
