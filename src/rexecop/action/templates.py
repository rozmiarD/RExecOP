from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rexecop.errors import RExecOpValidationError

ACTION_TEMPLATE_LIBRARY_SCHEMA = "rexecop.action_template_library.v0.1"
ACTION_TEMPLATE_SCOPE = "1.0"

_TEMPLATES: dict[str, dict[str, Any]] = {
    "http.simple-get": {
        "id": "http.simple-get",
        "scope": ACTION_TEMPLATE_SCOPE,
        "backend": "http_api",
        "title": "HTTP simple GET",
        "summary": "Read-only GET with bounded response and optional bearer auth refs.",
        "shape": {
            "method": "GET",
            "path": "/",
            "unwrap": "",
            "max_response_bytes": 2048,
            "mutating": False,
            "public_projection": {
                "safe_fields": [
                    "output.status_code",
                    "output.error_class",
                    "output.body_snippet",
                ],
            },
        },
        "connector_hints": {
            "base_url_secret_ref": "<connector>_base_url",
            "auth.secret_ref": "<connector>_api_token",
        },
    },
    "shell.readonly-allowlist": {
        "id": "shell.readonly-allowlist",
        "scope": ACTION_TEMPLATE_SCOPE,
        "backend": "local_shell_readonly",
        "title": "Read-only shell allowlist skeleton",
        "summary": "Allowlisted command entry for local_shell_readonly connectors.",
        "command_shape": {
            "command": "",
            "args": [],
            "public_projection": {
                "safe_fields": ["output.stdout", "output.stderr"],
            },
        },
        "allowlist_entry": {
            "action": "",
            "command": "",
            "args": [],
        },
        "connector_hints": {
            "max_output_bytes": 4096,
        },
    },
    "ssh.readonly-allowlist": {
        "id": "ssh.readonly-allowlist",
        "scope": ACTION_TEMPLATE_SCOPE,
        "backend": "ssh_readonly",
        "title": "Read-only SSH allowlist skeleton",
        "summary": "Allowlisted command entry for ssh_readonly connectors.",
        "command_shape": {
            "command": "",
            "args": [],
            "public_projection": {
                "safe_fields": ["output.stdout", "output.stderr"],
            },
        },
        "allowlist_entry": {
            "action": "",
            "command": "",
            "args": [],
        },
        "connector_hints": {
            "max_output_bytes": 4096,
            "identity_file_secret_ref": "<connector>_identity_file",
        },
    },
}

_DEFAULT_TEMPLATE_BY_BACKEND = {
    "http_api": "http.simple-get",
    "local_shell_readonly": "shell.readonly-allowlist",
    "ssh_readonly": "ssh.readonly-allowlist",
}


def list_action_templates() -> dict[str, Any]:
    """List built-in M5 action configuration templates."""
    return {
        "schema": ACTION_TEMPLATE_LIBRARY_SCHEMA,
        "scope": ACTION_TEMPLATE_SCOPE,
        "templates": [
            {
                "id": template["id"],
                "backend": template["backend"],
                "title": template["title"],
                "summary": template["summary"],
            }
            for template in _TEMPLATES.values()
        ],
        "non_claims": [
            "Templates provide readonly skeletons only in scope 1.0.",
            "Templates do not execute backend IO or resolve secret values.",
            "Profile connector contracts override template defaults when declared.",
        ],
    }


def get_action_template(template_id: str) -> dict[str, Any]:
    template = _TEMPLATES.get(template_id.strip())
    if template is None:
        raise RExecOpValidationError(f"unknown action template: {template_id}")
    return dict(template)


def default_template_id_for_backend(backend: str) -> str | None:
    return _DEFAULT_TEMPLATE_BY_BACKEND.get(str(backend or "").strip())


def resolve_template_id(
    *,
    backend: str,
    template_id: str | None = None,
) -> str | None:
    if template_id:
        template = get_action_template(template_id)
        if template["backend"] != backend:
            raise RExecOpValidationError(
                f"template {template_id} does not match backend {backend or 'missing'}"
            )
        return template_id
    return default_template_id_for_backend(backend)


def template_provenance_for_step(
    *,
    backend: str,
    action: str,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    template_id = default_template_id_for_backend(backend)
    if template_id is None:
        return {
            "available": False,
            "scope": ACTION_TEMPLATE_SCOPE,
            "reason": f"no built-in template for backend {backend or 'missing'}",
        }
    template = get_action_template(template_id)
    contract_shape = _contract_shape(backend, contract, action)
    if contract_shape is None:
        return {
            "available": True,
            "scope": ACTION_TEMPLATE_SCOPE,
            "template_id": template_id,
            "match": "skeleton",
            "summary": "profile does not declare a shape; built-in template provides skeleton",
        }
    if _shape_matches_template(backend, contract_shape, template):
        return {
            "available": True,
            "scope": ACTION_TEMPLATE_SCOPE,
            "template_id": template_id,
            "match": "profile_declared",
            "summary": "profile-declared shape is compatible with built-in template",
        }
    return {
        "available": True,
        "scope": ACTION_TEMPLATE_SCOPE,
        "template_id": template_id,
        "match": "profile_override",
        "summary": "profile-declared shape differs from built-in template defaults",
    }


def http_shape_from_template(
    template_id: str,
    *,
    path: str | None = None,
) -> dict[str, Any]:
    template = get_action_template(template_id)
    if template["backend"] != "http_api":
        raise RExecOpValidationError(f"template is not http_api: {template_id}")
    shape = dict(template["shape"])
    if path is not None and str(path).strip():
        shape["path"] = str(path).strip()
    return shape


def command_shape_from_template(template_id: str, *, action: str) -> dict[str, Any]:
    template = get_action_template(template_id)
    if template["backend"] not in {"local_shell_readonly", "ssh_readonly"}:
        raise RExecOpValidationError(f"template is not command-backed: {template_id}")
    shape = dict(template["command_shape"])
    shape["command"] = str(shape.get("command") or "").strip()
    shape["args"] = list(shape.get("args") or [])
    return shape


def allowlist_entry_from_template(
    template_id: str,
    *,
    action: str,
    command: str,
    args: list[str] | None = None,
) -> dict[str, Any]:
    template = get_action_template(template_id)
    entry = dict(template["allowlist_entry"])
    entry["action"] = action
    entry["command"] = command
    entry["args"] = list(args or [])
    return entry


def _contract_shape(
    backend: str,
    contract: Mapping[str, Any],
    action: str,
) -> dict[str, Any] | None:
    if backend == "http_api":
        action_shapes = contract.get("action_shapes")
        shape = action_shapes.get(action) if isinstance(action_shapes, Mapping) else None
        return dict(shape) if isinstance(shape, Mapping) else None
    command_shapes = contract.get("command_shapes")
    shape = command_shapes.get(action) if isinstance(command_shapes, Mapping) else None
    return dict(shape) if isinstance(shape, Mapping) else None


def _shape_matches_template(
    backend: str,
    contract_shape: Mapping[str, Any],
    template: Mapping[str, Any],
) -> bool:
    if backend == "http_api":
        template_shape = template.get("shape")
        if not isinstance(template_shape, Mapping):
            return False
        method = str(contract_shape.get("method") or "GET").upper()
        return method == str(template_shape.get("method") or "GET").upper()
    template_shape = template.get("command_shape")
    if not isinstance(template_shape, Mapping):
        return False
    return "command" in contract_shape and "args" in contract_shape