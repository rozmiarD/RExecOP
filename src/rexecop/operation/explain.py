from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rexecop.adapters.govengine_port.contracts import is_mutating_mode
from rexecop.adapters.sclite_port.contracts import ARTIFACT_SLOTS, SCLITE_SCHEMA_REFS
from rexecop.catalog.digest import profile_snapshot_digest, yaml_document_digest
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan

OPERATION_EXPLAIN_SCHEMA = "rexecop.operation_explain.v0.1"


def explain_operation(operation: Operation, plan: OperationPlan) -> dict[str, Any]:
    """Build a stable, redacted operator explanation for a stored plan."""
    return {
        "schema": OPERATION_EXPLAIN_SCHEMA,
        "operation": _operation_summary(operation, plan),
        "bindings": _bindings(operation, plan),
        "governance": _governance(operation),
        "workflow": _workflow(plan),
        "runtime_controls": _runtime_controls(operation, plan),
        "expected_sclite_artifacts": _expected_sclite_artifacts(),
        "sensitivity_filtering": {
            "status": "redacted",
            "omitted": [
                "environment connector configuration",
                "secret refs and resolved secrets",
                "raw backend output",
                "private catalog/environment paths outside runtime metadata",
            ],
        },
        "safe_next_actions": _safe_next_actions(operation),
        "non_claims": [
            "Does not execute work.",
            "Does not approve operators.",
            "Does not treat receipt exports as SCLite truth.",
            "Does not recompute GovEngine policy reasoning.",
        ],
    }


def _operation_summary(operation: Operation, plan: OperationPlan) -> dict[str, Any]:
    return {
        "operation_id": operation.id,
        "state": operation.state,
        "profile": operation.profile,
        "environment": operation.environment,
        "intent": operation.intent,
        "target": operation.target,
        "mode": operation.mode,
        "risk": plan.risk,
        "created_at": operation.created_at,
        "updated_at": operation.updated_at,
    }


def _bindings(operation: Operation, plan: OperationPlan) -> dict[str, Any]:
    metadata = operation.metadata
    profile_root_raw = str(metadata.get("profile_root") or "").strip()
    environment_path_raw = str(metadata.get("environment_path") or "").strip()
    profile_root = Path(profile_root_raw) if profile_root_raw else None
    environment_path = Path(environment_path_raw) if environment_path_raw else None
    profile_digest = ""
    environment_digest = ""
    if profile_root is not None and profile_root.exists():
        profile_digest = profile_snapshot_digest(profile_root)
    if environment_path is not None and environment_path.is_file():
        environment_digest = yaml_document_digest(environment_path)
    catalog_binding = dict(plan.catalog_binding)
    return {
        "profile_digest": profile_digest,
        "environment_digest": environment_digest,
        "catalog_binding": catalog_binding,
        "catalog_digests": {
            "catalog_digest": catalog_binding.get("catalog_digest", ""),
            "target_descriptor_digest": catalog_binding.get("target_descriptor_digest", ""),
            "operation_descriptor_digest": catalog_binding.get("operation_descriptor_digest", ""),
            "profile_digest": catalog_binding.get("profile_digest", ""),
            "environment_digest": catalog_binding.get("environment_digest", ""),
        },
        "http_action_bindings": dict(metadata.get("http_action_bindings") or {}),
    }


def _governance(operation: Operation) -> dict[str, Any]:
    metadata = operation.metadata
    enforcement = metadata.get("policy_enforcement")
    if not isinstance(enforcement, Mapping):
        enforcement = {}
    plan = enforcement.get("plan")
    if not isinstance(plan, Mapping):
        plan = {}
    admission = enforcement.get("admission")
    if not isinstance(admission, Mapping):
        admission = {}
    verdict = metadata.get("policy_verdict")
    if not isinstance(verdict, Mapping):
        verdict = {}
    return {
        "govengine_decision_type": operation.govengine_decision_type,
        "govengine_decision_summary": operation.govengine_decision_summary,
        "policy_verdict": {
            "decision": str(verdict.get("decision") or ""),
            "reason_code": str(verdict.get("reason_code") or ""),
            "blockers": [str(item) for item in verdict.get("blockers") or []],
        },
        "policy_enforcement": {
            "plan_id": str(plan.get("plan_id") or ""),
            "plan_status": str(plan.get("status") or ""),
            "plan_reason_code": str(plan.get("reason_code") or ""),
            "plan_blockers": [str(item) for item in plan.get("blockers") or []],
            "plan_digest": str(enforcement.get("plan_digest") or ""),
            "admission_id": str(admission.get("decision_id") or ""),
            "admission_outcome": str(admission.get("outcome") or ""),
            "admission_digest": str(enforcement.get("admission_digest") or ""),
        },
    }


def _workflow(plan: OperationPlan) -> dict[str, Any]:
    workflow = dict(plan.workflow)
    return {
        "workflow_id": str(workflow.get("id") or ""),
        "step_count": len(plan.planned_steps),
        "required_connectors": list(plan.required_connectors),
        "steps": [_redacted_step(step) for step in plan.planned_steps],
        "pause_safe_points": list(plan.pause_safe_points),
        "retry_policy_summary": dict(plan.retry_policy_summary),
        "rollback_available": bool(plan.rollback_available),
        "rollback_mode": str((workflow.get("rollback") or {}).get("mode") or "")
        if isinstance(workflow.get("rollback"), Mapping)
        else "",
    }


def _redacted_step(step: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(step.get("id") or ""),
        "type": str(step.get("type") or ""),
        "connector": str(step.get("connector") or ""),
        "action": str(step.get("action") or ""),
        "pause_safe": bool(step.get("pause_safe", False)),
    }


def _runtime_controls(operation: Operation, plan: OperationPlan) -> dict[str, Any]:
    mutating = is_mutating_mode(operation.mode)
    return {
        "mutating": mutating,
        "preflight_available": bool(plan.govengine_request_preview),
        "postflight_available": "produce_receipt" in {
            str(step.get("action") or "") for step in plan.planned_steps
        },
        "rollback_available": bool(plan.rollback_available),
        "mutation_contract_complete": _mutation_contract_complete(operation, plan)
        if mutating
        else None,
    }


def _mutation_contract_complete(operation: Operation, plan: OperationPlan) -> bool:
    metadata = operation.metadata
    return bool(
        plan.rollback_available
        and metadata.get("policy_enforcement")
        and plan.govengine_request_preview.get("policy_decision")
    )


def _expected_sclite_artifacts() -> list[dict[str, str]]:
    return [
        {
            "role": role,
            "schema_ref": SCLITE_SCHEMA_REFS[role],
            "authority": "sclite",
        }
        for role in ARTIFACT_SLOTS
    ]


def _safe_next_actions(operation: Operation) -> list[str]:
    if operation.state == "planned":
        return [f"rexecop start --operation {operation.id}"]
    if operation.state == "waiting_for_approval":
        return [
            f"rexecop approve --operation {operation.id} --by <operator>",
            f"rexecop status --operation {operation.id}",
        ]
    if operation.state == "approved":
        return [f"rexecop start --operation {operation.id}"]
    if operation.state == "failed":
        return [
            f"rexecop history --operation {operation.id}",
            f"rexecop escalate --operation {operation.id}",
        ]
    return [f"rexecop status --operation {operation.id}"]
