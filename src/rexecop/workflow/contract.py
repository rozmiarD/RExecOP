from __future__ import annotations

from rexecop.connectors.action_shape import validate_http_action_shape
from rexecop.environment.model import Environment
from rexecop.errors import RExecOpValidationError
from rexecop.profile.loader import LoadedProfile
from rexecop.workflow.model import Workflow

ALLOWED_WORKFLOW_STEP_TYPES = frozenset({"internal", "connector", "evidence"})


def validate_workflow_contract(
    workflow: Workflow,
    environment: Environment,
    profile: LoadedProfile | None = None,
) -> None:
    """Ensure workflow steps stay within declared connectors and supported types."""
    if not workflow.steps:
        raise RExecOpValidationError(f"workflow has no steps: {workflow.id}")

    for step in workflow.steps:
        step_type = str(step.type or "").strip()
        if step_type not in ALLOWED_WORKFLOW_STEP_TYPES:
            raise RExecOpValidationError(
                f"unsupported workflow step type: {step_type} ({step.id})"
            )
        if step.metadata.get("continue_on_error") is True and (
            workflow.mode != "read_only" or step_type != "connector"
        ):
            raise RExecOpValidationError(
                f"continue_on_error requires read_only connector step: {step.id}"
            )
        if step_type != "connector":
            continue
        connector_name = str(step.connector or "").strip()
        if not connector_name:
            raise RExecOpValidationError(
                f"connector step missing connector name: {step.id}"
            )
        config = environment.connectors.get(connector_name)
        if not isinstance(config, dict):
            raise RExecOpValidationError(
                f"connector not configured in environment: {connector_name}"
            )
        if not bool(config.get("enabled", True)):
            raise RExecOpValidationError(f"connector disabled: {connector_name}")
        if profile is not None:
            _validate_profile_connector_step(
                connector_name=connector_name,
                action=step.action,
                config=config,
                profile=profile,
            )


def _validate_profile_connector_step(
    *,
    connector_name: str,
    action: str,
    config: dict[str, object],
    profile: LoadedProfile,
) -> None:
    contract = profile.connector_contract(connector_name)
    if contract is None:
        return
    capabilities = contract.get("capabilities")
    if isinstance(capabilities, list) and action not in capabilities:
        raise RExecOpValidationError(
            f"connector action not declared by profile: {connector_name}.{action}"
        )
    expected_backend = str(contract.get("backend") or "").strip()
    actual_backend = str(config.get("backend") or config.get("mode") or "").strip()
    if expected_backend and actual_backend != expected_backend:
        raise RExecOpValidationError(
            f"connector backend mismatch for {connector_name}: "
            f"expected {expected_backend}, got {actual_backend or 'missing'}"
        )
    if actual_backend == "http_api":
        validate_http_action_shape(
            connector_name=connector_name,
            action=action,
            connector_contract=contract,
            connector_config=config,
        )
    command_shapes = contract.get("command_shapes")
    if not isinstance(command_shapes, dict):
        return
    shape = command_shapes.get(action)
    if not isinstance(shape, dict):
        raise RExecOpValidationError(
            f"command shape not declared by profile: {connector_name}.{action}"
        )
    allowlist = config.get("allowlist")
    if not isinstance(allowlist, list):
        raise RExecOpValidationError(
            f"connector allowlist required for command shape: {connector_name}.{action}"
        )
    matches = [
        item
        for item in allowlist
        if isinstance(item, dict)
        and str(item.get("action") or item.get("command") or "") == action
    ]
    if len(matches) != 1:
        raise RExecOpValidationError(
            f"exactly one allowlist entry required for {connector_name}.{action}"
        )
    entry = matches[0]
    expected_command = str(shape.get("command") or "").strip()
    expected_args = shape.get("args") or []
    actual_command = str(entry.get("command") or "").strip()
    actual_args = entry.get("args") or []
    if (
        not expected_command
        or not isinstance(expected_args, list)
        or actual_command != expected_command
        or actual_args != expected_args
    ):
        raise RExecOpValidationError(
            f"allowlist command shape mismatch for {connector_name}.{action}"
        )
