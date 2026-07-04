from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from rexecop.action.surface import _backend_class, _resolve_context
from rexecop.catalog.digest import canonical_digest
from rexecop.catalog.service import compile_operation_descriptor
from rexecop.environment.sanitize import validate_no_inline_secrets
from rexecop.errors import RExecOpValidationError
from rexecop.workflow.loader import load_workflow

ACTION_CONFIGURE_SCHEMA = "rexecop.action_configure.v0.1"


def configure_action(
    intent: str,
    *,
    env: Path,
    profile: str | Path | None = None,
    catalog: Path | None = None,
    target: str | None = None,
    write_patch: Path | None = None,
) -> dict[str, Any]:
    """Generate a bounded action configuration patch without mutating env YAML."""
    context = _resolve_context(profile=profile, env=env, catalog=catalog, target=target)
    operation = compile_operation_descriptor(context.profile, intent)
    workflow = load_workflow(context.profile.resolve_workflow_path(intent))
    source = _load_environment_document(env)
    validate_no_inline_secrets(source["environment"])
    operations: list[dict[str, Any]] = []
    for step in workflow.steps:
        if step.type != "connector":
            continue
        contract = context.profile.connector_contract(step.connector) or {}
        env_connectors = source["environment"].setdefault("connectors", {})
        if not isinstance(env_connectors, dict):
            raise RExecOpValidationError("environment.connectors must be a mapping")
        config = env_connectors.get(step.connector)
        if not isinstance(config, dict):
            config = {}
        backend = _backend_class(contract, config) or str(contract.get("backend") or "").strip()
        if backend == "http_api":
            operations.extend(_http_patch_operations(step.connector, step.action, contract, config))
        elif backend in {"local_shell_readonly", "ssh_readonly"}:
            operations.extend(
                _command_patch_operations(step.connector, step.action, backend, contract, config)
            )
        else:
            operations.append(
                {
                    "op": "unsupported",
                    "path": f"/environment/connectors/{step.connector}",
                    "reason": f"no minimal configure template for backend {backend or 'missing'}",
                }
            )
    patch = {
        "schema": "rexecop.action_configure_patch.v0.1",
        "operations": operations,
    }
    patch_json = json.dumps(patch, indent=2, sort_keys=True)
    if write_patch is not None:
        write_patch.write_text(patch_json + "\n", encoding="utf-8")
    return {
        "schema": ACTION_CONFIGURE_SCHEMA,
        "status": "patch_available" if operations else "no_change",
        "dry_run": True,
        "write_patch": str(write_patch) if write_patch is not None else "",
        "action": {
            "id": operation.id,
            "operation_descriptor_digest": operation.digest,
        },
        "environment": {
            "id": context.environment.id if context.environment is not None else "",
            "profile": context.environment.profile if context.environment is not None else "",
        },
        "patch": patch,
        "patch_digest": "sha256:" + canonical_digest(patch),
        "non_claims": [
            "Does not modify the environment YAML.",
            "Does not write secret values.",
            "Does not read any secret store.",
            "Does not create an execution request.",
            "Does not request or imply GovEngine admission.",
            "Does not emit SCLite truth artifacts.",
        ],
    }


def _load_environment_document(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("environment"), dict):
        raise RExecOpValidationError(f"invalid environment yaml: {path}")
    return data


def _http_patch_operations(
    connector: str,
    action: str,
    contract: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    if not config:
        operations.append(
            {
                "op": "add",
                "path": f"/environment/connectors/{connector}",
                "value": {"enabled": False, "backend": "http_api", "actions": {}},
            }
        )
    elif str(config.get("backend") or "") != "http_api":
        operations.append(
            {
                "op": "add",
                "path": f"/environment/connectors/{connector}/backend",
                "value": "http_api",
            }
        )
    action_shapes = contract.get("action_shapes")
    shape = action_shapes.get(action) if isinstance(action_shapes, dict) else None
    if not isinstance(shape, dict):
        operations.append(
            {
                "op": "unsupported",
                "path": f"/environment/connectors/{connector}/actions/{action}",
                "reason": "profile connector does not declare action_shapes for this action",
            }
        )
        return operations
    actions = config.get("actions")
    existing = actions.get(action) if isinstance(actions, dict) else None
    if not isinstance(actions, dict):
        operations.append(
            {
                "op": "add",
                "path": f"/environment/connectors/{connector}/actions",
                "value": {},
            }
        )
    if not isinstance(existing, dict):
        operations.append(
            {
                "op": "add",
                "path": f"/environment/connectors/{connector}/actions/{action}",
                "value": _http_action_template(shape),
            }
        )
    return operations


def _http_action_template(shape: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "method",
        "path",
        "query",
        "body",
        "unwrap",
        "pagination",
        "mutating",
        "max_response_bytes",
    }
    return {key: shape[key] for key in allowed if key in shape}


def _command_patch_operations(
    connector: str,
    action: str,
    backend: str,
    contract: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    if not config:
        operations.append(
            {
                "op": "add",
                "path": f"/environment/connectors/{connector}",
                "value": {"enabled": False, "backend": backend, "allowlist": []},
            }
        )
    elif str(config.get("backend") or "") != backend:
        operations.append(
            {
                "op": "add",
                "path": f"/environment/connectors/{connector}/backend",
                "value": backend,
            }
        )
    command_shapes = contract.get("command_shapes")
    shape = command_shapes.get(action) if isinstance(command_shapes, dict) else None
    if not isinstance(shape, dict):
        operations.append(
            {
                "op": "unsupported",
                "path": f"/environment/connectors/{connector}/allowlist",
                "reason": "profile connector does not declare command_shapes for this action",
            }
        )
        return operations
    allowlist = config.get("allowlist")
    if not isinstance(allowlist, list):
        operations.append(
            {
                "op": "add",
                "path": f"/environment/connectors/{connector}/allowlist",
                "value": [],
            }
        )
        allowlist = []
    if not any(
        isinstance(item, dict) and str(item.get("action") or "") == action
        for item in allowlist
    ):
        operations.append(
            {
                "op": "add",
                "path": f"/environment/connectors/{connector}/allowlist/-",
                "value": {
                    "action": action,
                    "command": str(shape.get("command") or ""),
                    "args": list(shape.get("args") or []),
                },
            }
        )
    return operations
