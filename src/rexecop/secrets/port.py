from __future__ import annotations

from typing import Protocol


class SecretResolver(Protocol):
    def resolve(self, secret_ref: str) -> str: ...
