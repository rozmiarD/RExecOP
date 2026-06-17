from __future__ import annotations

import os
from pathlib import Path

import yaml

from rexecop.errors import RExecOpValidationError
from rexecop.secrets.port import SecretResolver


class EnvSecretResolver:
    """Resolve secret_ref from REXECOP_SECRET_<REF> environment variables."""

    def resolve(self, secret_ref: str) -> str:
        ref = secret_ref.strip()
        if not ref:
            raise RExecOpValidationError("secret_ref is required")
        env_key = f"REXECOP_SECRET_{ref.upper().replace('-', '_')}"
        value = os.environ.get(env_key)
        if value is None or value == "":
            raise RExecOpValidationError(f"secret not found in environment: {env_key}")
        return value


class FileSecretResolver:
    """Resolve secret_ref from an operator-managed YAML file outside git."""

    def __init__(self, path: Path | None = None) -> None:
        configured = path or os.environ.get("REXECOP_SECRETS_FILE")
        self.path = Path(configured).expanduser() if configured else None

    def resolve(self, secret_ref: str) -> str:
        if self.path is None or not self.path.is_file():
            raise RExecOpValidationError("REXECOP_SECRETS_FILE is not configured")
        data = yaml.safe_load(self.path.read_text())
        if not isinstance(data, dict):
            raise RExecOpValidationError(f"invalid secrets file: {self.path}")
        secrets = data.get("secrets")
        if not isinstance(secrets, dict):
            raise RExecOpValidationError(f"secrets mapping missing in: {self.path}")
        ref = secret_ref.strip()
        value = secrets.get(ref)
        if value is None or str(value) == "":
            raise RExecOpValidationError(f"secret_ref not found: {ref}")
        return str(value)


class ChainedSecretResolver:
    def __init__(self, *resolvers: SecretResolver) -> None:
        self.resolvers = resolvers

    def resolve(self, secret_ref: str) -> str:
        last_error: Exception | None = None
        for resolver in self.resolvers:
            try:
                return resolver.resolve(secret_ref)
            except RExecOpValidationError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RExecOpValidationError(f"secret_ref not resolved: {secret_ref}")


def default_secret_resolver() -> ChainedSecretResolver:
    return ChainedSecretResolver(EnvSecretResolver(), FileSecretResolver())
