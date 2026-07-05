from __future__ import annotations

from pathlib import Path
from typing import Any

from govengine import explain_policy_evaluation

from rexecop.catalog.service import CatalogService
from rexecop.environment.loader import load_environment
from rexecop.environment.sanitize import validate_no_inline_secrets
from rexecop.environment.targets import validate_operation_target
from rexecop.errors import RExecOpValidationError
from rexecop.policy.lifecycle import describe_policy_pack_lifecycle
from rexecop.policy.operation import build_operation_policy_request
from rexecop.policy.pack import compile_environment_policy_pack
from rexecop.profile.loader import load_profile
from rexecop.profile.resolver import resolve_profile_path
from rexecop.workflow.contract import validate_workflow_contract
from rexecop.workflow.loader import load_workflow

POLICY_EXPLAIN_SCHEMA = "rexecop.policy_explain.v0.1"
SUPPORTED_POLICY_EXPLAIN_MODES = frozenset(
    {"observe", "dry_run", "apply", "emergency_readonly", "recovery"}
)


def explain_operation_policy(
    *,
    profile_path: str | Path | None,
    environment_path: Path | None,
    intent: str,
    target: str,
    mode: str,
    catalog_path: Path | None = None,
) -> dict[str, Any]:
    if mode not in SUPPORTED_POLICY_EXPLAIN_MODES:
        raise RExecOpValidationError(f"unsupported mode: {mode}")

    catalog_resolution = None
    if catalog_path is not None:
        catalog_resolution = CatalogService(catalog_path.expanduser().resolve()).resolve_operation(
            target,
            intent,
        )
        if not catalog_resolution.applicability.applicable:
            raise RExecOpValidationError(
                "catalog operation is not applicable: "
                f"{catalog_resolution.applicability.status}"
            )
        if profile_path is not None:
            supplied_profile = resolve_profile_path(profile_path).resolve()
            if supplied_profile.is_file():
                supplied_profile = supplied_profile.parent
            if supplied_profile != catalog_resolution.target.profile_path.resolve():
                raise RExecOpValidationError("catalog profile does not match supplied profile")
        if environment_path is not None and (
            environment_path.expanduser().resolve()
            != catalog_resolution.target.environment_path.resolve()
        ):
            raise RExecOpValidationError("catalog environment does not match supplied environment")
        profile_path = catalog_resolution.target.profile_path
        environment_path = catalog_resolution.target.environment_path
        target = catalog_resolution.target.environment_target

    if profile_path is None:
        raise RExecOpValidationError("profile is required without a target catalog")
    if environment_path is None:
        raise RExecOpValidationError("environment is required without a target catalog")

    profile = load_profile(resolve_profile_path(profile_path))
    environment = load_environment(environment_path)
    validate_no_inline_secrets(environment.as_dict())
    if environment.profile and environment.profile != profile.name:
        raise RExecOpValidationError(
            f"environment profile {environment.profile} does not match {profile.name}"
        )

    intent_meta = profile.intent_metadata(intent)
    intent_modes = intent_meta.get("modes")
    if intent_meta.get("enforce_declared_modes") is True and (
        not isinstance(intent_modes, list) or mode not in intent_modes
    ):
        raise RExecOpValidationError(f"mode {mode} not declared for intent: {intent}")
    workflow = load_workflow(profile.resolve_workflow_path(intent))
    validate_operation_target(environment, target)
    validate_workflow_contract(workflow, environment, profile)
    policy_pack = compile_environment_policy_pack(environment.policy_pack)
    if policy_pack is None:
        raise RExecOpValidationError("environment policy_pack is required for policy explain")

    risk = str(intent_meta.get("risk") or workflow.risk)
    request = build_operation_policy_request(
        operation_id="explain",
        profile=profile.name,
        environment=environment,
        intent=intent,
        target=target,
        mode=mode,
        risk=risk,
    )
    explanation = explain_policy_evaluation(request, policy_pack)
    return {
        "schema": POLICY_EXPLAIN_SCHEMA,
        "status": explanation.status,
        "profile": profile.name,
        "environment": environment.id,
        "intent": intent,
        "target": target,
        "mode": mode,
        "risk": risk,
        "policy": {
            "policy_id": policy_pack.policy_id,
            "version": policy_pack.version,
            "lifecycle": describe_policy_pack_lifecycle(
                environment.policy_pack,
                policy_pack,
            ),
            "request": {
                "request_id": request["request_id"],
                "subject_ref": request["subject_ref"],
            },
            "explanation": explanation.as_dict(),
        },
        "catalog_binding": (
            catalog_resolution.binding.as_dict() if catalog_resolution is not None else {}
        ),
        "non_claims": [
            "RExecOp does not compute GovEngine policy reasoning.",
            "RExecOp does not execute work from policy explain.",
            "SCLite remains truth authority for emitted artifacts.",
        ],
    }
