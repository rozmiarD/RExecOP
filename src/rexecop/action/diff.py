from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rexecop.action.configure import configure_action
from rexecop.action.surface import _backend_class, _resolve_context
from rexecop.catalog.digest import canonical_digest, yaml_document_digest
from rexecop.catalog.service import compile_operation_descriptor
from rexecop.connectors.action_shape import (
    canonical_http_action_shape,
    http_action_shape_digest,
)
from rexecop.environment.sanitize import validate_no_inline_secrets
from rexecop.errors import RExecOpValidationError
from rexecop.workflow.loader import load_workflow
from rexecop.yaml_input import load_yaml_file

ACTION_DIFF_SCHEMA = "rexecop.action_diff.v0.1"


def diff_action(
    intent: str,
    *,
    env: Path,
    profile: str | Path | None = None,
    catalog: Path | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """Compare profile connector contracts against operator environment bindings."""
    context = _resolve_context(profile=profile, env=env, catalog=catalog, target=target)
    operation = compile_operation_descriptor(context.profile, intent)
    workflow = load_workflow(context.profile.resolve_workflow_path(intent))
    _validate_environment_document(env)
    steps = [
        _diff_connector_step(context, step)
        for step in workflow.steps
        if step.type == "connector"
    ]
    blockers = [
        f"{item['connector']}:{item['action']}"
        for item in steps
        if item["status"] in {"drifted", "incomplete", "unsupported"}
    ]
    configure_result = configure_action(
        intent,
        profile=profile,
        env=env,
        catalog=catalog,
        target=target,
    )
    patch_operations = configure_result["patch"]["operations"]
    actionable_operations = [
        operation
        for operation in patch_operations
        if str(operation.get("op") or "") != "unsupported"
    ]
    return {
        "schema": ACTION_DIFF_SCHEMA,
        "status": "aligned" if not blockers else "drifted",
        "action": {
            "id": operation.id,
            "operation_descriptor_digest": operation.digest,
        },
        "environment": {
            "profile": context.environment.profile if context.environment is not None else "",
            "digest": (
                yaml_document_digest(context.environment_path)
                if context.environment_path is not None
                else ""
            ),
        },
        "steps": steps,
        "drift_summary": blockers,
        "configure_hint": {
            "schema": configure_result["schema"],
            "status": configure_result["status"],
            "patch_digest": configure_result["patch_digest"],
            "operation_count": len(actionable_operations),
            "unsupported_operation_count": len(patch_operations) - len(actionable_operations),
        },
        "non_claims": [
            "Does not execute backend IO.",
            "Does not modify the environment YAML.",
            "Does not print resolved secret values or connector endpoint configuration.",
            "Does not create an execution request.",
            "Does not request or imply GovEngine admission.",
            "Does not emit SCLite truth artifacts.",
            "Suggested patch output is advisory; review before any apply.",
        ],
    }


def _validate_environment_document(path: Path) -> None:
    data = load_yaml_file(path)
    if not isinstance(data, dict) or not isinstance(data.get("environment"), dict):
        raise RExecOpValidationError(f"invalid environment yaml: {path}")
    validate_no_inline_secrets(data["environment"])


def _diff_connector_step(context: Any, step: Any) -> dict[str, Any]:
    contract = context.profile.connector_contract(step.connector) or {}
    config = _connector_config(context, step.connector)
    backend = _backend_class(contract, config) or str(contract.get("backend") or "").strip()
    checks, drift_fields = _compare_connector_step(
        connector=step.connector,
        action=step.action,
        backend=backend,
        contract=contract,
        config=config,
    )
    status = _step_status(checks, backend=backend)
    expected_digest, actual_digest = _shape_digests(
        connector=step.connector,
        action=step.action,
        backend=backend,
        contract=contract,
        config=config,
    )
    return {
        "step_id": step.id,
        "connector": step.connector,
        "action": step.action,
        "backend_class": backend,
        "status": status,
        "checks": checks,
        "drift_fields": drift_fields,
        "expected_shape_digest": expected_digest,
        "actual_shape_digest": actual_digest,
    }


def _connector_config(context: Any, connector: str) -> dict[str, Any]:
    if context.environment is None:
        return {}
    config = context.environment.connectors.get(connector)
    return dict(config) if isinstance(config, Mapping) else {}


def _compare_connector_step(
    *,
    connector: str,
    action: str,
    backend: str,
    contract: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[list[dict[str, str]], list[str]]:
    checks: list[dict[str, str]] = []
    drift_fields: list[str] = []
    if not contract:
        checks.append(_check("profile_contract", "failed", "profile connector contract missing"))
        return checks, drift_fields
    checks.append(_check("profile_contract", "passed", "profile connector contract declared"))
    if not config:
        checks.append(_check("connector_configured", "failed", "connector missing in environment"))
        return checks, drift_fields
    checks.append(_check("connector_configured", "passed", "connector configured in environment"))
    if not bool(config.get("enabled", True)):
        checks.append(_check("connector_enabled", "failed", "connector disabled in environment"))
        drift_fields.append("enabled")
    else:
        checks.append(_check("connector_enabled", "passed", "connector enabled"))
    expected_backend = str(contract.get("backend") or "").strip()
    if expected_backend and backend != expected_backend:
        checks.append(
            _check(
                "backend_match",
                "failed",
                f"expected backend {expected_backend}, got {backend or 'missing'}",
            )
        )
        drift_fields.append("backend")
    else:
        checks.append(_check("backend_match", "passed", "backend matches profile contract"))
    if backend == "http_api":
        checks.extend(_http_checks(connector, action, contract, config, drift_fields))
    elif backend in {"local_shell_readonly", "ssh_readonly"}:
        checks.extend(_command_checks(connector, action, contract, config, drift_fields))
    elif backend == "static_fixture":
        checks.extend(_fixture_checks(connector, action, config, drift_fields))
    elif backend:
        checks.append(
            _check(
                "diff_support",
                "failed",
                f"no profile-vs-env diff template for backend {backend}",
            )
        )
    else:
        checks.append(_check("diff_support", "failed", "connector backend is missing"))
        drift_fields.append("backend")
    return checks, drift_fields


def _http_checks(
    connector: str,
    action: str,
    contract: Mapping[str, Any],
    config: Mapping[str, Any],
    drift_fields: list[str],
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    action_shapes = contract.get("action_shapes")
    shape = action_shapes.get(action) if isinstance(action_shapes, Mapping) else None
    if not isinstance(shape, Mapping):
        checks.append(
            _check(
                "profile_action_shape",
                "failed",
                "profile connector does not declare action_shapes for this action",
            )
        )
        return checks
    checks.append(_check("profile_action_shape", "passed", "profile action shape declared"))
    actions = config.get("actions")
    actual_spec = actions.get(action) if isinstance(actions, Mapping) else None
    if not isinstance(actual_spec, Mapping):
        checks.append(_check("action_configured", "failed", "http action missing in environment"))
        drift_fields.append(f"connectors.{connector}.actions.{action}")
        return checks
    checks.append(_check("action_configured", "passed", "http action configured in environment"))
    expected = canonical_http_action_shape(dict(shape), dict(shape))
    actual = canonical_http_action_shape(dict(actual_spec), dict(config))
    mismatched = [
        field
        for field in sorted(set(expected) | set(actual))
        if expected.get(field) != actual.get(field)
    ]
    if mismatched:
        checks.append(
            _check(
                "shape_match",
                "failed",
                "http action shape differs from profile contract",
            )
        )
        drift_fields.extend(mismatched)
    else:
        checks.append(_check("shape_match", "passed", "http action shape matches profile contract"))
    return checks


def _command_checks(
    connector: str,
    action: str,
    contract: Mapping[str, Any],
    config: Mapping[str, Any],
    drift_fields: list[str],
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    command_shapes = contract.get("command_shapes")
    shape = command_shapes.get(action) if isinstance(command_shapes, Mapping) else None
    if not isinstance(shape, Mapping):
        checks.append(
            _check(
                "profile_command_shape",
                "failed",
                "profile connector does not declare command_shapes for this action",
            )
        )
        return checks
    checks.append(_check("profile_command_shape", "passed", "profile command shape declared"))
    allowlist = config.get("allowlist")
    if not isinstance(allowlist, list):
        checks.append(_check("allowlist_configured", "failed", "allowlist missing in environment"))
        drift_fields.append(f"connectors.{connector}.allowlist")
        return checks
    entry = _find_allowlist_entry(allowlist, action)
    if entry is None:
        checks.append(
            _check("allowlist_entry", "failed", "allowlist entry missing for action")
        )
        drift_fields.append(f"connectors.{connector}.allowlist")
        return checks
    checks.append(_check("allowlist_entry", "passed", "allowlist entry present"))
    expected_command = str(shape.get("command") or "").strip()
    expected_args = list(shape.get("args") or [])
    actual_command = str(entry.get("command") or "").strip()
    actual_args = list(entry.get("args") or [])
    if actual_command != expected_command:
        checks.append(_check("command_match", "failed", "allowlist command differs from profile"))
        drift_fields.append("command")
    elif actual_args != expected_args:
        checks.append(_check("command_match", "failed", "allowlist args differ from profile"))
        drift_fields.append("args")
    else:
        checks.append(_check("command_match", "passed", "allowlist matches profile command shape"))
    return checks


def _fixture_checks(
    connector: str,
    action: str,
    config: Mapping[str, Any],
    drift_fields: list[str],
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    actions = config.get("actions")
    spec = actions.get(action) if isinstance(actions, Mapping) else None
    if not isinstance(spec, Mapping):
        checks.append(
            _check("action_configured", "failed", "static_fixture action missing in environment")
        )
        drift_fields.append(f"connectors.{connector}.actions.{action}")
    else:
        checks.append(
            _check("action_configured", "passed", "static_fixture action configured in environment")
        )
        checks.append(_check("shape_match", "passed", "fixture action binding present"))
    return checks


def _shape_digests(
    *,
    connector: str,
    action: str,
    backend: str,
    contract: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[str, str]:
    if backend == "http_api":
        action_shapes = contract.get("action_shapes")
        shape = action_shapes.get(action) if isinstance(action_shapes, Mapping) else None
        if isinstance(shape, Mapping):
            expected = http_action_shape_digest(
                canonical_http_action_shape(dict(shape), dict(shape))
            )
        else:
            expected = ""
        actions = config.get("actions")
        actual_spec = actions.get(action) if isinstance(actions, Mapping) else None
        if isinstance(actual_spec, Mapping):
            actual = http_action_shape_digest(
                canonical_http_action_shape(dict(actual_spec), dict(config))
            )
        else:
            actual = ""
        return expected, actual
    if backend in {"local_shell_readonly", "ssh_readonly"}:
        command_shapes = contract.get("command_shapes")
        shape = command_shapes.get(action) if isinstance(command_shapes, Mapping) else None
        if isinstance(shape, Mapping):
            expected = "sha256:" + canonical_digest(
                {
                    "schema": "rexecop.command_shape_projection.v0.1",
                    "connector": connector,
                    "action": action,
                    "shape": dict(shape),
                }
            )
        else:
            expected = ""
        allowlist = config.get("allowlist")
        entry = _find_allowlist_entry(allowlist, action) if isinstance(allowlist, list) else None
        if isinstance(entry, Mapping):
            actual = "sha256:" + canonical_digest(
                {
                    "schema": "rexecop.command_shape_projection.v0.1",
                    "connector": connector,
                    "action": action,
                    "shape": {
                        "command": str(entry.get("command") or ""),
                        "args": list(entry.get("args") or []),
                    },
                }
            )
        else:
            actual = ""
        return expected, actual
    return "", ""


def _find_allowlist_entry(allowlist: list[Any], action: str) -> Mapping[str, Any] | None:
    for item in allowlist:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("action") or item.get("command") or "") == action:
            return item
    return None


def _step_status(checks: list[dict[str, str]], *, backend: str) -> str:
    if any(check["id"] == "diff_support" and check["status"] == "failed" for check in checks):
        return "unsupported"
    if any(
        check["id"] == "connector_configured" and check["status"] == "failed"
        for check in checks
    ):
        return "incomplete"
    if any(
        check["id"] in {"action_configured", "allowlist_configured", "allowlist_entry"}
        and check["status"] == "failed"
        for check in checks
    ):
        return "incomplete"
    if any(check["status"] == "failed" for check in checks):
        return "drifted"
    return "aligned"


def _check(check_id: str, status: str, summary: str) -> dict[str, str]:
    return {"id": check_id, "status": status, "summary": summary}
