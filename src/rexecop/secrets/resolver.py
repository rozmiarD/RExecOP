from __future__ import annotations

import os
import stat
from pathlib import Path

from rexecop.errors import RExecOpValidationError
from rexecop.evidence.redaction import register_secret_value
from rexecop.secrets.port import SecretResolver
from rexecop.secrets.reference import _EnvKeyClaims, _SecretRefEnvCollision
from rexecop.yaml_input import load_yaml_bytes

MAX_SECRETS_FILE_BYTES = 1024 * 1024


class EnvSecretResolver:
    """Resolve secret_ref from REXECOP_SECRET_<REF> environment variables."""

    def __init__(self) -> None:
        self._claims = _EnvKeyClaims()

    def resolve(self, secret_ref: str) -> str:
        _, env_key = self._claims.claim(secret_ref)
        value = os.environ.get(env_key)
        if value is None or value == "":
            raise RExecOpValidationError(f"secret not found in environment: {env_key}")
        register_secret_value(value)
        return value


class FileSecretResolver:
    """Resolve secret_ref from an operator-managed YAML file outside git."""

    def __init__(self, path: Path | None = None) -> None:
        configured = path or os.environ.get("REXECOP_SECRETS_FILE")
        self.path = Path(configured).expanduser() if configured else None

    def resolve(self, secret_ref: str) -> str:
        if self.path is None:
            raise RExecOpValidationError("REXECOP_SECRETS_FILE is not configured")
        data = load_yaml_bytes(
            self._read_secure_file(),
            max_bytes=MAX_SECRETS_FILE_BYTES,
        )
        if not isinstance(data, dict):
            raise RExecOpValidationError("invalid REXECOP_SECRETS_FILE")
        secrets = data.get("secrets")
        if not isinstance(secrets, dict):
            raise RExecOpValidationError("secrets mapping missing in REXECOP_SECRETS_FILE")
        ref = secret_ref.strip()
        value = secrets.get(ref)
        if value is None or str(value) == "":
            raise RExecOpValidationError(f"secret_ref not found: {ref}")
        resolved = str(value)
        register_secret_value(resolved)
        return resolved

    def _read_secure_file(self) -> bytes:
        assert self.path is not None
        try:
            info = self.path.lstat()
        except FileNotFoundError as exc:
            raise RExecOpValidationError("REXECOP_SECRETS_FILE is not configured") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise RExecOpValidationError("REXECOP_SECRETS_FILE must be a regular file")
        if info.st_uid != os.getuid():
            raise RExecOpValidationError("REXECOP_SECRETS_FILE must be owned by the current user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise RExecOpValidationError(
                "REXECOP_SECRETS_FILE permissions must be 0600 or stricter"
            )
        if info.st_size > MAX_SECRETS_FILE_BYTES:
            raise RExecOpValidationError("REXECOP_SECRETS_FILE exceeds the size limit")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except OSError as exc:
            raise RExecOpValidationError(
                "REXECOP_SECRETS_FILE cannot be opened safely"
            ) from exc
        try:
            opened_info = os.fstat(descriptor)
            if (opened_info.st_dev, opened_info.st_ino) != (info.st_dev, info.st_ino):
                raise RExecOpValidationError(
                    "REXECOP_SECRETS_FILE changed during validation"
                )
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                content = handle.read(MAX_SECRETS_FILE_BYTES + 1)
                if len(content) > MAX_SECRETS_FILE_BYTES:
                    raise RExecOpValidationError(
                        "REXECOP_SECRETS_FILE exceeds the size limit"
                    )
                return content
        finally:
            if descriptor >= 0:
                os.close(descriptor)


class ChainedSecretResolver:
    def __init__(self, *resolvers: SecretResolver) -> None:
        self.resolvers = resolvers

    def resolve(self, secret_ref: str) -> str:
        last_error: Exception | None = None
        for resolver in self.resolvers:
            try:
                return resolver.resolve(secret_ref)
            except _SecretRefEnvCollision:
                raise
            except RExecOpValidationError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RExecOpValidationError(f"secret_ref not resolved: {secret_ref}")


def default_secret_resolver() -> ChainedSecretResolver:
    return ChainedSecretResolver(EnvSecretResolver(), FileSecretResolver())
