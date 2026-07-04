from __future__ import annotations

from pathlib import Path
from typing import Any

from rexecop.environment.loader import load_environment
from rexecop.secrets.doctor import collect_secret_ref_bindings

SECRETS_SUGGEST_REF_SCHEMA = "rexecop.secrets_suggest_ref.v0.1"


def suggest_secret_refs(
    *,
    env_path: Path,
    connector: str | None = None,
) -> dict[str, Any]:
    """Suggest bounded secret reference names without reading any secret store."""
    environment = load_environment(env_path)
    connectors = {
        name: config
        for name, config in environment.connectors.items()
        if connector is None or name == connector
    }
    suggestions = [
        item
        for name, config in sorted(connectors.items())
        if isinstance(config, dict)
        for item in _connector_suggestions(name, config)
    ]
    existing_refs = [
        {
            "connector": _connector_from_path(binding["path"]),
            "path": binding["path"],
            "ref": binding["ref"],
            "status": "existing",
        }
        for binding in collect_secret_ref_bindings({"connectors": connectors})
        if connector is None or _connector_from_path(binding["path"]) == connector
    ]
    return {
        "schema": SECRETS_SUGGEST_REF_SCHEMA,
        "environment": {
            "id": environment.id,
            "profile": environment.profile,
        },
        "connector_filter": connector or "",
        "existing_refs": existing_refs,
        "suggestions": suggestions,
        "non_claims": [
            "Does not read REXECOP_SECRETS_FILE.",
            "Does not read REXECOP_SECRET_* environment values.",
            "Does not validate that suggested refs resolve.",
            "Does not print secret values.",
        ],
    }


def _connector_suggestions(connector: str, config: dict[str, Any]) -> list[dict[str, str]]:
    backend = str(config.get("backend") or config.get("mode") or "").strip()
    suggestions: list[dict[str, str]] = []
    if backend == "http_api":
        if not config.get("base_url_secret_ref") and not config.get("base_url"):
            suggestions.append(
                _suggestion(connector, "base_url_secret_ref", f"{connector}_base_url")
            )
        auth = config.get("auth")
        if not isinstance(auth, dict) or not str(auth.get("secret_ref") or "").strip():
            suggestions.append(_suggestion(connector, "auth.secret_ref", f"{connector}_api_token"))
        tls = config.get("tls")
        if isinstance(tls, dict) and not str(tls.get("ca_file_secret_ref") or "").strip():
            suggestions.append(
                _suggestion(connector, "tls.ca_file_secret_ref", f"{connector}_ca_file")
            )
    if backend == "ssh_readonly" and not str(config.get("identity_file_secret_ref") or "").strip():
        suggestions.append(
            _suggestion(connector, "identity_file_secret_ref", f"{connector}_identity_file")
        )
    return suggestions


def _suggestion(connector: str, path: str, ref: str) -> dict[str, str]:
    return {
        "connector": connector,
        "path": f"connectors.{connector}.{path}",
        "suggested_ref": _normalize_ref(ref),
        "status": "suggested",
    }


def _normalize_ref(value: str) -> str:
    return "_".join(part for part in value.strip().lower().replace("-", "_").split("_") if part)


def _connector_from_path(path: str) -> str:
    parts = path.split(".")
    if len(parts) >= 2 and parts[0] == "connectors":
        return parts[1]
    return ""
