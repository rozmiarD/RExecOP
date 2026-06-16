from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sclite.artifacts import validate_artifact
from sclite.bundles import (
    REVIEW_BUNDLE_REQUIRED_FILES,
    materialize_review_bundle,
    validate_review_bundle_shape,
)
from sclite.integrity import artifact_descriptor

from rexecop.adapters.sclite_port.contracts import SCLITE_SCHEMA_REFS
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan

SCLITE_ARTIFACT_AUTHORITY = "sclite_artifact"
REAL_EMITTER_NAME = "sclite"


@dataclass
class SCLiteEmissionResult:
    operation_id: str
    bundle_dir: str
    sclite_refs: dict[str, Any]
    artifacts: dict[str, dict[str, Any]]
    review_record: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "authority": SCLITE_ARTIFACT_AUTHORITY,
            "emitter": REAL_EMITTER_NAME,
            "bundle_dir": self.bundle_dir,
            "sclite_refs": dict(self.sclite_refs),
            "artifact_roles": sorted(self.artifacts.keys()),
            "review_verdict": self.review_record.get("verdict"),
        }


def _parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _link(role: str, artifact: dict[str, Any]) -> dict[str, Any]:
    return {"role": role, "descriptor": artifact_descriptor(artifact)}


def _validate(role: str, artifact: dict[str, Any]) -> dict[str, Any]:
    schema_key = {
        "intent_contract": "intent_contract.v0.2",
        "policy_decision": "policy_decision.v0.2",
        "execution_contract": "execution_contract.v0.2",
        "execution_ticket": "execution_ticket.v0.2",
        "execution_receipt": "execution_receipt.v0.2",
        "evidence_contract": "evidence_contract.v0.2",
    }[role]
    validate_artifact(artifact, schema_key)
    return artifact


def _rexecop_mode(mode: str) -> str:
    if mode in {"apply", "recovery"}:
        return "live"
    return "dry_run"


def _policy_decision_value(govengine_decision_type: str) -> str:
    if govengine_decision_type == "allowed":
        return "allow_prepare"
    if govengine_decision_type in {
        "approval_required",
        "maintenance_window_required",
        "backup_required",
    }:
        return "owner_approval_required"
    return "deny"


def _ticket_approval_status(operation: Operation) -> str:
    if operation.govengine_decision_type in {
        "approval_required",
        "maintenance_window_required",
        "backup_required",
    }:
        return "owner_approval_required"
    if _rexecop_mode(operation.mode) == "dry_run":
        return "approved_for_dry_run"
    if operation.govengine_decision_type == "allowed":
        return "approved"
    return "rejected"


def _execution_plan_steps(plan: OperationPlan) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(plan.planned_steps, start=1):
        tool = str(step.get("connector") or step.get("type") or "internal")
        action = str(step.get("action") or step.get("id") or "")
        steps.append({"step": index, "tool": tool, "args": [action] if action else []})
    return steps


def build_intent_contract(
    operation: Operation,
    plan: OperationPlan,
    *,
    requested_by: str = "operator",
) -> dict[str, Any]:
    created_at = operation.created_at
    capability_mode = _rexecop_mode(plan.mode)
    artifact = {
        "artifact_type": "intent_contract",
        "schema_version": "v0.2",
        "schema_ref": SCLITE_SCHEMA_REFS["intent_contract"],
        "intent_id": f"intent-{operation.id}",
        "created_at": created_at,
        "actor": {
            "kind": "operator",
            "carrier": "rexecop",
            "label": requested_by,
        },
        "intent": {
            "category": plan.profile,
            "summary": f"{plan.intent} on {plan.target} via profile {plan.profile}",
        },
        "requested_capability": {
            "name": plan.intent,
            "mode": capability_mode,
        },
        "target": {
            "host": plan.target,
            "uri": f"rexecop:{plan.environment}/{plan.target}",
        },
        "constraints": [
            f"profile:{plan.profile}",
            f"environment:{plan.environment}",
            f"risk:{plan.risk}",
        ],
        "authority": {
            "intent_is_authority": False,
            "requires_policy_decision": True,
            "requires_execution_ticket": True,
        },
        "non_claims": [
            "intent_does_not_authorize_execution",
            "rexecop_intent_is_planning_input_only",
        ],
    }
    return _validate("intent_contract", artifact)


def build_policy_decision(
    operation: Operation,
    plan: OperationPlan,
    intent_contract: dict[str, Any],
) -> dict[str, Any]:
    decision = _policy_decision_value(operation.govengine_decision_type or "allowed")
    network_effect = "none_in_dry_run" if _rexecop_mode(plan.mode) == "dry_run" else "bounded"
    artifact = {
        "artifact_type": "policy_decision",
        "schema_version": "v0.2",
        "schema_ref": SCLITE_SCHEMA_REFS["policy_decision"],
        "decision_id": f"policy-{operation.id}",
        "created_at": operation.updated_at,
        "decision": decision,
        "links": {"intent": _link("intent_contract", intent_contract)},
        "scope": {
            "scope_profile": plan.profile,
            "target_host": plan.target,
            "target_in_scope": decision != "deny",
        },
        "capability": {
            "class": plan.risk,
            "name": plan.intent,
            "network_effect": network_effect,
        },
        "risk": {
            "level": plan.risk,
            "reason": operation.govengine_decision_summary or "rexecop governance summary",
        },
        "constraints": [
            f"mode:{plan.mode}",
            f"govengine:{operation.govengine_decision_type or 'not_evaluated'}",
        ],
        "reason_codes": [operation.govengine_decision_type or "dry_run_default"],
    }
    return _validate("policy_decision", artifact)


def build_execution_contract(
    operation: Operation,
    plan: OperationPlan,
    intent_contract: dict[str, Any],
    policy_decision: dict[str, Any],
) -> dict[str, Any]:
    steps = _execution_plan_steps(plan)
    primary_tool = steps[0]["tool"] if steps else "rexecop_internal"
    normalized_args = [plan.target]
    capability_mode = _rexecop_mode(plan.mode)
    artifact = {
        "artifact_type": "execution_contract",
        "schema_version": "v0.2",
        "schema_ref": SCLITE_SCHEMA_REFS["execution_contract"],
        "contract_id": f"exec-contract-{operation.id}",
        "created_at": operation.updated_at,
        "links": {
            "intent": _link("intent_contract", intent_contract),
            "policy_decision": _link("policy_decision", policy_decision),
        },
        "target_binding": {
            "target": plan.target,
            "target_host": plan.target,
            "target_in_scope": policy_decision["decision"] != "deny",
        },
        "execution_shape": {
            "tool": primary_tool,
            "normalized_args": normalized_args,
            "plan": steps,
        },
        "execution_bounds": {
            "mode": capability_mode,
            "max_commands": max(len(steps), 1),
            "network_execution_allowed": capability_mode != "dry_run",
            "timeout_seconds": 0,
        },
        "expected_receipt": {
            "required": True,
            "schema_ref": SCLITE_SCHEMA_REFS["execution_receipt"],
        },
        "non_claims": [
            "execution_contract_is_not_runtime_execution",
            "execution_contract_requires_ticket_before_use",
        ],
    }
    return _validate("execution_contract", artifact)


def build_execution_ticket(
    operation: Operation,
    plan: OperationPlan,
    intent_contract: dict[str, Any],
    policy_decision: dict[str, Any],
    execution_contract: dict[str, Any],
) -> dict[str, Any]:
    contract_digest = artifact_descriptor(execution_contract)["digest"]
    start = _parse_timestamp(operation.created_at)
    end = start + timedelta(hours=24)
    approval_status = _ticket_approval_status(operation)
    artifact = {
        "artifact_type": "execution_ticket",
        "schema_version": "v0.2",
        "schema_ref": SCLITE_SCHEMA_REFS["execution_ticket"],
        "ticket_id": f"ticket-{operation.id}",
        "created_at": operation.updated_at,
        "links": {
            "intent": _link("intent_contract", intent_contract),
            "policy_decision": _link("policy_decision", policy_decision),
            "execution_contract": _link("execution_contract", execution_contract),
        },
        "approval": {
            "approval_id": f"approval-{operation.id}",
            "approver_kind": "govengine",
            "status": approval_status,
        },
        "validity": {
            "not_before": start.isoformat(),
            "not_after": end.isoformat(),
        },
        "execution_limits": {
            "mode": _rexecop_mode(plan.mode),
            "max_runs": 1,
            "one_shot": True,
        },
        "integrity": {
            "profile": "sclite-v0.2-integrity-only",
            "ticket_binds_execution_contract_digest": contract_digest,
        },
        "signature": {
            "identity_signature_required": False,
            "mode": "not_signed_integrity_only_fixture",
            "note": "RExecOp Phase 3B integrity-only ticket binding.",
        },
        "non_claims": [
            "ticket_does_not_prove_live_execution",
            "runtime_must_enforce_ticket_bounds",
        ],
    }
    return _validate("execution_ticket", artifact)


def build_execution_receipt(
    operation: Operation,
    plan: OperationPlan,
    execution_contract: dict[str, Any],
    execution_ticket: dict[str, Any],
    *,
    completed_at: str | None = None,
) -> dict[str, Any]:
    ended_at = completed_at or operation.updated_at
    capability_mode = _rexecop_mode(plan.mode)
    steps = _execution_plan_steps(plan)
    artifact = {
        "artifact_type": "execution_receipt",
        "schema_version": "v0.2",
        "schema_ref": SCLITE_SCHEMA_REFS["execution_receipt"],
        "receipt_id": f"receipt-{operation.id}",
        "created_at": ended_at,
        "links": {
            "execution_contract": _link("execution_contract", execution_contract),
            "execution_ticket": _link("execution_ticket", execution_ticket),
        },
        "runtime": {
            "name": "rexecop",
            "version": "phase-3b",
            "mode": capability_mode,
        },
        "execution": {
            "started_at": operation.created_at,
            "ended_at": ended_at,
            "planned_command_count": len(steps),
            "executed_command_count": 0,
            "network_execution_performed": False,
        },
        "outcome": {
            "status": capability_mode if operation.state == "planned" else operation.state,
            "returncode": 0,
            "summary": f"RExecOp lifecycle receipt for operation {operation.id}",
            "stderr_present": False,
            "stdout_present": False,
        },
        "evidence_refs": [
            {"kind": "evidence_bundle", "path": REVIEW_BUNDLE_REQUIRED_FILES["evidence_contract"]}
        ],
        "non_claims": [
            "receipt_does_not_include_raw_logs",
            "phase_3b_does_not_claim_connector_execution",
        ],
    }
    return _validate("execution_receipt", artifact)


def build_evidence_contract(
    operation: Operation,
    execution_receipt: dict[str, Any],
    execution_ticket: dict[str, Any],
) -> dict[str, Any]:
    artifact = {
        "artifact_type": "evidence_contract",
        "schema_version": "v0.2",
        "schema_ref": SCLITE_SCHEMA_REFS["evidence_contract"],
        "evidence_contract_id": f"evidence-{operation.id}",
        "created_at": operation.updated_at,
        "links": {
            "execution_receipt": _link("execution_receipt", execution_receipt),
            "execution_ticket": _link("execution_ticket", execution_ticket),
        },
        "claims": [
            {
                "id": "lifecycle_emitted",
                "statement": "RExecOp emitted a linked v0.2 lifecycle bundle for the operation.",
                "status": "met",
            }
        ],
        "non_claims": [
            "does_not_include_private_runtime_logs",
            "does_not_prove_live_connector_execution",
        ],
        "replay": {
            "mode": "static_bundle_verification",
            "live_execution_required": False,
        },
        "verification": {
            "commands": [
                "sclite validate-chain artifact_chain_manifest.json",
            ]
        },
    }
    return _validate("evidence_contract", artifact)


def build_lifecycle_artifacts(
    operation: Operation,
    plan: OperationPlan,
) -> dict[str, dict[str, Any]]:
    intent_contract = build_intent_contract(operation, plan, requested_by=operation.requested_by)
    policy_decision = build_policy_decision(operation, plan, intent_contract)
    execution_contract = build_execution_contract(
        operation, plan, intent_contract, policy_decision
    )
    execution_ticket = build_execution_ticket(
        operation, plan, intent_contract, policy_decision, execution_contract
    )
    execution_receipt = build_execution_receipt(
        operation, plan, execution_contract, execution_ticket
    )
    evidence_contract = build_evidence_contract(
        operation, execution_receipt, execution_ticket
    )
    return {
        "intent_contract": intent_contract,
        "policy_decision": policy_decision,
        "execution_contract": execution_contract,
        "execution_ticket": execution_ticket,
        "execution_receipt": execution_receipt,
        "evidence_contract": evidence_contract,
    }


def build_sclite_refs(
    bundle_dir: str,
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for role, artifact in artifacts.items():
        descriptor = artifact_descriptor(artifact)
        filename = REVIEW_BUNDLE_REQUIRED_FILES[role]
        refs[role] = {
            "sclite_schema_ref": descriptor["schema_ref"],
            "descriptor_path": f"{bundle_dir}/{filename}",
            "digest": descriptor["digest"],
            "status": "emitted",
        }
    return refs


class SCLiteArtifactEmitter:
    """Emit validated SCLite v0.2 lifecycle artifacts for an operation."""

    def emit_intent_contract(
        self,
        operation: Operation,
        plan: OperationPlan,
    ) -> dict[str, Any]:
        return build_intent_contract(operation, plan, requested_by=operation.requested_by)

    def emit_operation_bundle(
        self,
        *,
        operation: Operation,
        plan: OperationPlan,
        bundle_dir: str,
    ) -> SCLiteEmissionResult:
        artifacts = build_lifecycle_artifacts(operation, plan)
        review_record = materialize_review_bundle(
            bundle_dir,
            artifacts,
            chain_id=f"rexecop-{operation.id}",
            created_at=operation.created_at,
            generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        )
        validate_review_bundle_shape(bundle_dir)
        refs = build_sclite_refs(bundle_dir, artifacts)
        return SCLiteEmissionResult(
            operation_id=operation.id,
            bundle_dir=bundle_dir,
            sclite_refs=refs,
            artifacts=artifacts,
            review_record=review_record,
        )
