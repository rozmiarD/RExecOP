from __future__ import annotations

import re
from typing import Any

from rexecop import __version__
from rexecop.action.configure import ACTION_CONFIGURE_SCHEMA
from rexecop.action.diff import ACTION_DIFF_SCHEMA
from rexecop.action.policy_impact import ACTION_POLICY_IMPACT_SCHEMA
from rexecop.action.surface import (
    ACTION_LIST_SCHEMA,
    ACTION_PREVIEW_SCHEMA,
    ACTION_SHOW_SCHEMA,
    ACTION_VALIDATE_SCHEMA,
)
from rexecop.action.templates import ACTION_TEMPLATE_LIBRARY_SCHEMA
from rexecop.adapters.sclite_port.contracts import ARTIFACT_SLOTS, SCLITE_SCHEMA_REFS
from rexecop.errors import RExecOpValidationError
from rexecop.execution.model import (
    EXECUTION_RECEIPT_SCHEMA_VERSION,
    EXECUTION_REQUEST_SCHEMA_VERSION,
    TYPED_EXECUTION_BINDING_SCHEMA,
)
from rexecop.execution.typed_spec import TYPED_EXECUTION_SCHEMA_VERSION
from rexecop.runtime.init import INIT_SCHEMA
from rexecop.secrets.doctor import SECRETS_DOCTOR_SCHEMA

STACK_CONTRACT_COMPATIBILITY_SCHEMA = "rexecop.stack_contract_compatibility.v0.1"
DOCTOR_REPORT_SCHEMA = "rexecop.doctor_report.v0.1"
OPERATION_EXPLAIN_SCHEMA = "rexecop.operation_explain.v0.1"
PROFILE_CONTRACT_SCHEMA = "rexecop.profile_contract.v0.1"
COMPATIBILITY_POLICY = "unknown_major_fail_closed"

_SCLITE_SCHEMA_REF_PATTERN = re.compile(r"schemas/([a-z_]+)\.(v\d+\.\d+)\.schema\.json$")

SUPPORTED_SCLITE_ARTIFACT_VERSIONS: dict[str, str] = {
    "intent_contract": "v0.2",
    "policy_decision": "v0.2",
    "execution_contract": "v0.2",
    "execution_ticket": "v0.3",
    "execution_receipt": "v0.2",
    "evidence_contract": "v0.2",
    "trigger_decision": "v0.1",
    "automation_chain": "v0.1",
}

STACK_SCLITE_SCHEMA_REF_ROLES: tuple[str, ...] = (
    *ARTIFACT_SLOTS,
    "trigger_decision",
    "automation_chain",
)

REXECOP_RUNTIME_PROJECTIONS: tuple[dict[str, Any], ...] = (
    {
        "surface_id": "step_execution_spec",
        "owner": "rexecop.execution.typed_spec",
        "schema": "rexecop.step_execution_spec.v0.1",
        "supported_versions": (TYPED_EXECUTION_SCHEMA_VERSION,),
    },
    {
        "surface_id": "command_execution_spec",
        "owner": "rexecop.execution.typed_spec",
        "schema": "rexecop.command_execution_spec.v0.1",
        "supported_versions": (TYPED_EXECUTION_SCHEMA_VERSION,),
    },
    {
        "surface_id": "http_action_execution_spec",
        "owner": "rexecop.execution.typed_spec",
        "schema": "rexecop.http_action_execution_spec.v0.1",
        "supported_versions": (TYPED_EXECUTION_SCHEMA_VERSION,),
    },
    {
        "surface_id": "static_fixture_execution_spec",
        "owner": "rexecop.execution.typed_spec",
        "schema": "rexecop.static_fixture_execution_spec.v0.1",
        "supported_versions": (TYPED_EXECUTION_SCHEMA_VERSION,),
    },
    {
        "surface_id": "typed_execution_binding",
        "owner": "rexecop.execution.model",
        "schema": TYPED_EXECUTION_BINDING_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "execution_request",
        "owner": "rexecop.execution.model",
        "schema": "rexecop.execution_request.v0.2",
        "supported_versions": (EXECUTION_REQUEST_SCHEMA_VERSION,),
    },
    {
        "surface_id": "execution_receipt",
        "owner": "rexecop.execution.model",
        "schema": "rexecop.execution_receipt.v0.2",
        "supported_versions": (EXECUTION_RECEIPT_SCHEMA_VERSION,),
    },
    {
        "surface_id": "runtime_manifest",
        "owner": "rexecop.runtime.init",
        "schema": INIT_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "doctor_report",
        "owner": "rexecop.runtime.doctor",
        "schema": DOCTOR_REPORT_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "stack_contract_compatibility",
        "owner": "rexecop.runtime.contract_compatibility",
        "schema": STACK_CONTRACT_COMPATIBILITY_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "operation_explain",
        "owner": "rexecop.operation.explain",
        "schema": OPERATION_EXPLAIN_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "action_configure",
        "owner": "rexecop.action.configure",
        "schema": ACTION_CONFIGURE_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "action_template_library",
        "owner": "rexecop.action.templates",
        "schema": ACTION_TEMPLATE_LIBRARY_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "action_list",
        "owner": "rexecop.action.surface",
        "schema": ACTION_LIST_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "action_show",
        "owner": "rexecop.action.surface",
        "schema": ACTION_SHOW_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "action_preview",
        "owner": "rexecop.action.surface",
        "schema": ACTION_PREVIEW_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "action_validate",
        "owner": "rexecop.action.surface",
        "schema": ACTION_VALIDATE_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "action_diff",
        "owner": "rexecop.action.diff",
        "schema": ACTION_DIFF_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "action_policy_impact",
        "owner": "rexecop.action.policy_impact",
        "schema": ACTION_POLICY_IMPACT_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "secrets_doctor",
        "owner": "rexecop.secrets.doctor",
        "schema": SECRETS_DOCTOR_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "profile_contract",
        "owner": "rexecop.profile.contract",
        "schema": PROFILE_CONTRACT_SCHEMA,
        "supported_versions": ("v0.1",),
    },
    {
        "surface_id": "truth_path_projection",
        "owner": "rexecop.truth_path",
        "schema": "rexecop.truth_path_projection.v0.1",
        "supported_versions": ("v0.1",),
    },
)

REQUIRED_RUNTIME_PROJECTION_SURFACES = (
    "step_execution_spec",
    "execution_request",
    "execution_receipt",
    "runtime_manifest",
    "doctor_report",
    "operation_explain",
    "action_configure",
    "stack_contract_compatibility",
)

REXECOP_EXPECTED_GOVENGINE_CONTRACTS: tuple[dict[str, str], ...] = (
    {"surface_id": "policy_request", "schema_version": "v0.1"},
    {"surface_id": "policy_verdict", "schema_version": "v0.1"},
    {"surface_id": "policy_enforcement_plan", "schema_version": "v0.1"},
    {"surface_id": "runtime_control_projection", "schema_version": "v0.1"},
    {"surface_id": "gov_admission_decision", "schema_version": "v0.1"},
    {"surface_id": "trigger_planning_request", "schema_version": "v0.1"},
    {"surface_id": "supervisor_action_request", "schema_version": "v0.1"},
    {"surface_id": "typed_execution_governance_request", "schema_version": "v0.1"},
    {"surface_id": "typed_execution_governance_projection", "schema_version": "v0.1"},
    {"surface_id": "typed_execution_stack_compatibility", "schema_version": "v0.1"},
    {"surface_id": "typed_execution_control_catalog", "schema_version": "v0.1"},
    {"surface_id": "governance_trace", "schema_version": "v0.1"},
)

REXECOP_OPTIONAL_GOVENGINE_CONTRACTS: tuple[dict[str, str], ...] = (
    {"surface_id": "automation_transition_request", "schema_version": "v0.1"},
    {"surface_id": "automation_transition_explanation", "schema_version": "v0.1"},
)

_PROJECTION_INDEX = {item["surface_id"]: item for item in REXECOP_RUNTIME_PROJECTIONS}


def contract_major_version(version: str) -> str:
    text = str(version or "").strip()
    if not text:
        return ""
    return text.split(".", 1)[0]


def validate_rexecop_projection_version(surface_id: str, version: str) -> None:
    entry = _PROJECTION_INDEX.get(str(surface_id or "").strip())
    if entry is None:
        raise RExecOpValidationError(f"unsupported_runtime_projection_surface:{surface_id}")
    supported = tuple(entry.get("supported_versions") or ())
    normalized = str(version or "").strip()
    if normalized in supported:
        return
    supported_majors = {contract_major_version(item) for item in supported}
    major = contract_major_version(normalized)
    if major and major not in supported_majors:
        raise RExecOpValidationError(
            f"unsupported_runtime_projection_major_version:{surface_id}:{normalized}"
        )
    raise RExecOpValidationError(
        f"unsupported_runtime_projection_version:{surface_id}:{normalized}"
    )


def _sclite_schema_version(schema_ref: str) -> tuple[str, str]:
    match = _SCLITE_SCHEMA_REF_PATTERN.match(str(schema_ref or "").strip())
    if not match:
        raise RExecOpValidationError(f"unsupported_sclite_schema_ref:{schema_ref}")
    return match.group(1), match.group(2)


def supported_sclite_artifact_refs() -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for role in STACK_SCLITE_SCHEMA_REF_ROLES:
        schema_ref = SCLITE_SCHEMA_REFS[role]
        artifact, version = _sclite_schema_version(schema_ref)
        refs.append(
            {
                "role": role,
                "artifact": artifact,
                "schema_version": version,
                "schema_ref": schema_ref,
            }
        )
    return refs


def validate_sclite_artifact_pins() -> list[str]:
    errors: list[str] = []
    for role in STACK_SCLITE_SCHEMA_REF_ROLES:
        schema_ref = SCLITE_SCHEMA_REFS[role]
        artifact, version = _sclite_schema_version(schema_ref)
        expected = SUPPORTED_SCLITE_ARTIFACT_VERSIONS.get(artifact)
        if expected is None:
            errors.append(f"unsupported_sclite_artifact:{artifact}")
            continue
        if version != expected:
            errors.append(f"sclite_artifact_version_mismatch:{artifact}:{version}!={expected}")
    return errors


def rexecop_runtime_projection_matrix() -> dict[str, Any]:
    return {
        "schema": STACK_CONTRACT_COMPATIBILITY_SCHEMA,
        "rexecop_version": __version__,
        "compatibility_policy": COMPATIBILITY_POLICY,
        "projections": [dict(item) for item in REXECOP_RUNTIME_PROJECTIONS],
        "required_surfaces": list(REQUIRED_RUNTIME_PROJECTION_SURFACES),
    }


def build_govengine_contract_compatibility_request(
    *,
    request_id: str = "rexecop-govengine-contracts",
) -> dict[str, Any]:
    declared_contracts = [dict(item) for item in REXECOP_EXPECTED_GOVENGINE_CONTRACTS]
    declared_contracts.extend(_supported_optional_govengine_contracts())
    return {
        "schema_version": "v0.1",
        "request_id": request_id,
        "consumer": "rexecop",
        "consumer_version": __version__,
        "declared_contracts": declared_contracts,
    }


def evaluate_govengine_contract_compatibility(
    *,
    request_id: str = "rexecop-govengine-contracts",
) -> dict[str, Any]:
    from govengine import evaluate_contract_compatibility, supported_contract_report

    request = build_govengine_contract_compatibility_request(request_id=request_id)
    report = evaluate_contract_compatibility(request)
    catalog = supported_contract_report()
    payload = report.as_dict()
    return {
        "schema": STACK_CONTRACT_COMPATIBILITY_SCHEMA,
        "status": payload["status"],
        "request_id": payload["request_id"],
        "report_digest": payload["report_digest"],
        "govengine_version": payload["govengine_version"],
        "matched_contracts": payload["matched_contracts"],
        "unsupported_contracts": payload["unsupported_contracts"],
        "missing_contracts": payload["missing_contracts"],
        "blockers": payload["blockers"],
        "govengine_contract_catalog": catalog,
        "rexecop_runtime_projections": rexecop_runtime_projection_matrix(),
        "compatibility": payload,
        "non_claims": list(payload["non_claims"]),
    }


def contract_versions_summary(
    *,
    profile_version: str = "",
) -> dict[str, Any]:
    govengine = evaluate_govengine_contract_compatibility()
    return {
        "compatibility_policy": COMPATIBILITY_POLICY,
        "rexecop_version": __version__,
        "govengine_version": govengine["govengine_version"],
        "profile_contract_version": profile_version,
        "runtime_projections": {
            item["surface_id"]: item["supported_versions"][0]
            for item in REXECOP_RUNTIME_PROJECTIONS
            if item.get("supported_versions")
        },
        "govengine_contracts": {
            item["surface_id"]: item["schema_version"]
            for item in (
                *REXECOP_EXPECTED_GOVENGINE_CONTRACTS,
                *_supported_optional_govengine_contracts(),
            )
        },
        "sclite_artifact_refs": {
            item["role"]: item["schema_version"] for item in supported_sclite_artifact_refs()
        },
        "status": govengine["status"],
        "blockers": list(govengine.get("blockers") or []),
    }


def _supported_optional_govengine_contracts() -> list[dict[str, str]]:
    try:
        from govengine import supported_contract_report
    except ImportError:
        return []
    try:
        catalog = supported_contract_report()
    except Exception:
        return []
    supported = {
        str(item.get("surface_id") or "")
        for item in catalog.get("contracts") or []
        if isinstance(item, dict) and item.get("status") == "supported"
    }
    return [
        dict(item)
        for item in REXECOP_OPTIONAL_GOVENGINE_CONTRACTS
        if item["surface_id"] in supported
    ]


def evaluate_stack_contract_compatibility(
    *,
    request_id: str = "rexecop-stack-contracts",
) -> dict[str, Any]:
    govengine = evaluate_govengine_contract_compatibility(request_id=request_id)
    sclite_errors = validate_sclite_artifact_pins()
    projection_errors: list[str] = []
    for surface_id in REQUIRED_RUNTIME_PROJECTION_SURFACES:
        if surface_id not in _PROJECTION_INDEX:
            projection_errors.append(f"missing_runtime_projection:{surface_id}")
    blockers = list(govengine.get("blockers") or [])
    blockers.extend(sclite_errors)
    blockers.extend(projection_errors)
    blockers = list(dict.fromkeys(item for item in blockers if item))
    status = "passed" if not blockers else "blocked"
    return {
        "schema": STACK_CONTRACT_COMPATIBILITY_SCHEMA,
        "status": status,
        "request_id": request_id,
        "reason_code": "stack_contract_compatibility_passed" if not blockers else blockers[0],
        "compatibility_policy": COMPATIBILITY_POLICY,
        "rexecop_version": __version__,
        "govengine_contracts": govengine,
        "runtime_projections": rexecop_runtime_projection_matrix(),
        "sclite_artifact_refs": supported_sclite_artifact_refs(),
        "profile_contract_surface": {
            "schema": PROFILE_CONTRACT_SCHEMA,
            "supported_versions": ["v0.1"],
        },
        "blockers": blockers,
        "non_claims": list(govengine.get("non_claims") or []),
    }
