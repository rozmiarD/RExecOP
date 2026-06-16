from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sclite.artifacts import validate_artifact
from sclite.bundles import (
    REVIEW_BUNDLE_REQUIRED_FILES,
    materialize_review_bundle,
    validate_review_bundle_shape,
)
from sclite.integrity import artifact_descriptor

from rexecop.adapters.sclite_port.contracts import SCLITE_ARTIFACT_AUTHORITY, SCLITE_SCHEMA_REFS
from rexecop.adapters.sclite_port.full_bundle import (
    FULL_BUNDLE_MANIFEST_PROFILE,
    KERNEL_GUARD_MANIFEST_FILE,
    build_receipt_bounded_evidence_contract,
    build_scoped_execution_receipt,
    build_scoped_execution_ticket,
    verify_full_bundle,
    write_full_bundle_sidecars,
    write_kernel_guard_manifest,
)
from rexecop.adapters.sclite_port.govengine_policy_bridge import (
    policy_reason_codes,
    policy_summary,
)
from rexecop.adapters.sclite_port.target_host import resolve_sclite_target_host
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan

REAL_EMITTER_NAME = "sclite"


@dataclass
class SCLiteEmissionResult:
    operation_id: str
    bundle_dir: str
    sclite_refs: dict[str, Any]
    artifacts: dict[str, dict[str, Any]]
    review_record: dict[str, Any]
    bundle_profile: str = "govengine_integration_v0.5"
    sidecars: dict[str, dict[str, Any]] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "authority": SCLITE_ARTIFACT_AUTHORITY,
            "emitter": REAL_EMITTER_NAME,
            "bundle_dir": self.bundle_dir,
            "bundle_profile": self.bundle_profile,
            "sclite_refs": dict(self.sclite_refs),
            "artifact_roles": sorted(self.artifacts.keys()),
            "sidecar_files": sorted((self.sidecars or {}).keys()),
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
        "execution_ticket": "execution_ticket.v0.3",
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
    if operation.metadata.get("manual_approval"):
        if _rexecop_mode(operation.mode) == "dry_run":
            return "approved_for_dry_run"
        return "approved"
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
    target_host = resolve_sclite_target_host(plan)
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
            "host": target_host,
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
    target_host = resolve_sclite_target_host(plan)
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
            "target_host": target_host,
            "target_in_scope": decision != "deny",
        },
        "capability": {
            "class": plan.risk,
            "name": plan.intent,
            "network_effect": network_effect,
        },
        "risk": {
            "level": plan.risk,
            "reason": policy_summary(operation),
        },
        "constraints": [
            f"mode:{plan.mode}",
            f"govengine:{operation.govengine_decision_type or 'not_evaluated'}",
        ],
        "reason_codes": policy_reason_codes(operation, plan),
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
    target_host = resolve_sclite_target_host(plan)
    normalized_args = [f"rexecop:{plan.environment}/{plan.target}"]
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
            "target": f"rexecop:{plan.environment}/{plan.target}",
            "target_host": target_host,
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


def build_lifecycle_artifacts(
    operation: Operation,
    plan: OperationPlan,
) -> dict[str, dict[str, Any]]:
    intent_contract = build_intent_contract(operation, plan, requested_by=operation.requested_by)
    policy_decision = build_policy_decision(operation, plan, intent_contract)
    execution_contract = build_execution_contract(
        operation, plan, intent_contract, policy_decision
    )
    execution_ticket = build_scoped_execution_ticket(
        operation,
        plan,
        intent_contract,
        policy_decision,
        execution_contract,
        ticket_approval_status=_ticket_approval_status(operation),
        parse_timestamp=_parse_timestamp,
        rexecop_mode=_rexecop_mode,
        link=_link,
    )
    execution_receipt = build_scoped_execution_receipt(
        operation,
        plan,
        execution_contract,
        execution_ticket,
        completed_at=None,
        rexecop_mode=_rexecop_mode,
        execution_plan_steps=_execution_plan_steps,
        link=_link,
        validate=_validate,
    )
    evidence_contract = build_receipt_bounded_evidence_contract(
        operation,
        execution_receipt,
        execution_ticket,
        link=_link,
        validate=_validate,
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
    *,
    sidecars: dict[str, dict[str, Any]] | None = None,
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
    for filename, sidecar in (sidecars or {}).items():
        refs[filename.replace(".json", "")] = {
            "sclite_schema_ref": sidecar.get("schema_ref"),
            "descriptor_path": f"{bundle_dir}/{filename}",
            "digest": artifact_descriptor(sidecar).get("digest"),
            "status": "emitted",
        }
    refs["kernel_guard_manifest"] = {
        "sclite_schema_ref": "schemas/kernel_guard_hmac_v1.schema.json",
        "descriptor_path": f"{bundle_dir}/{KERNEL_GUARD_MANIFEST_FILE}",
        "status": "emitted",
    }
    return refs


class SCLiteArtifactEmitter:
    """Emit GovEngine-integration-grade SCLite review bundles for an operation."""

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
        materialize_review_bundle(
            bundle_dir,
            artifacts,
            chain_id=f"rexecop-{operation.id}",
            created_at=operation.created_at,
            generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        )
        manifest_path = Path(bundle_dir) / "artifact_chain_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["profile"] = FULL_BUNDLE_MANIFEST_PROFILE
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        sidecars = write_full_bundle_sidecars(
            bundle_dir,
            operation_id=operation.id,
            created_at=operation.updated_at,
            execution_ticket=artifacts["execution_ticket"],
            link=_link,
        )
        write_kernel_guard_manifest(bundle_dir)
        validate_review_bundle_shape(bundle_dir)
        review_record = verify_full_bundle(bundle_dir, artifacts)
        refs = build_sclite_refs(bundle_dir, artifacts, sidecars=sidecars)
        return SCLiteEmissionResult(
            operation_id=operation.id,
            bundle_dir=bundle_dir,
            sclite_refs=refs,
            artifacts=artifacts,
            review_record=review_record,
            sidecars=sidecars,
        )
