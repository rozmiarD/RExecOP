from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sclite.bundles import review_bundle
from sclite.integrity import artifact_descriptor
from sclite.kernel_guard import build_kernel_guard_manifest
from sclite.tickets import normalized_args_digest, verify_ticket_use

from rexecop.adapters.sclite_port.contracts import SCLITE_SCHEMA_REFS
from rexecop.adapters.sclite_port.target_host import sclite_target_ref

FULL_BUNDLE_MANIFEST_PROFILE = "sclite-v0.5-rexecop-integrity"
REXECOP_RUNTIME_REF = "runtime:rexecop-executor"
REXECOP_FIXTURE_GUARD_KEY = "rexecop-fixture-guard-key"
REXECOP_FIXTURE_GUARD_KEY_ID = "rexecop-fixture-guard-key"

TRUST_PROFILE_REF_FILE = "trust_profile_ref.json"
CARRIER_PROFILE_REF_FILE = "carrier_profile_ref.json"
KERNEL_GUARD_MANIFEST_FILE = "kernel_guard_manifest.json"

FULL_BUNDLE_SIDECARS = (
    TRUST_PROFILE_REF_FILE,
    CARRIER_PROFILE_REF_FILE,
    KERNEL_GUARD_MANIFEST_FILE,
)


def _validate_ticket(artifact: dict[str, Any]) -> dict[str, Any]:
    from sclite.artifacts import validate_artifact

    schema_ref = str(artifact.get("schema_ref") or "")
    if "execution_ticket.v0.3" in schema_ref:
        validate_artifact(artifact, "execution_ticket.v0.3")
    else:
        validate_artifact(artifact, "execution_ticket.v0.2")
    return artifact


def build_scoped_execution_ticket(
    operation: Any,
    plan: Any,
    intent_contract: dict[str, Any],
    policy_decision: dict[str, Any],
    execution_contract: dict[str, Any],
    *,
    ticket_approval_status: str,
    parse_timestamp: Any,
    rexecop_mode: Any,
    link: Any,
) -> dict[str, Any]:
    contract_digest = artifact_descriptor(execution_contract)["digest"]
    normalized_args = list(execution_contract["execution_shape"]["normalized_args"])
    primary_tool = str(execution_contract["execution_shape"]["tool"])
    capability_mode = rexecop_mode(plan.mode)
    target_host = str(execution_contract["target_binding"]["target_host"])
    start = parse_timestamp(operation.created_at)
    end = start + __import__("datetime").timedelta(hours=24)
    ticket_id = f"scoped-ticket-{operation.id}"

    artifact = {
        "artifact_type": "execution_ticket",
        "schema_version": "v0.3",
        "schema_ref": "schemas/execution_ticket.v0.3.schema.json",
        "ticket_id": ticket_id,
        "ticket_profile": "scoped_execution_ticket",
        "created_at": operation.updated_at,
        "links": {
            "intent": link("intent_contract", intent_contract),
            "policy_decision": link("policy_decision", policy_decision),
            "execution_contract": link("execution_contract", execution_contract),
        },
        "approval": {
            "approval_id": f"approval-{operation.id}",
            "approver_kind": "govengine",
            "status": ticket_approval_status,
        },
        "validity": {
            "not_before": start.isoformat(),
            "not_after": end.isoformat(),
        },
        "execution_limits": {
            "mode": capability_mode,
            "max_runs": 1,
            "one_shot": True,
        },
        "spend_limits": {
            "max_uses": 1,
            "network_execution_allowed": capability_mode != "dry_run",
            "one_shot": True,
            "requires_evidence_contract": True,
            "requires_receipt": True,
        },
        "scope_binding": {
            "mode": capability_mode,
            "normalized_args_digest": normalized_args_digest(normalized_args),
            "target_host": target_host,
            "target_kind": "host",
            "target_ref": sclite_target_ref(target_host),
            "tool": primary_tool,
        },
        "subject_binding": {
            "issued_for_actor": f"operator:{operation.requested_by}",
            "session_ref": f"session:{operation.correlation_id or operation.id}",
            "usable_by_runtime": REXECOP_RUNTIME_REF,
        },
        "ticket_semantics": {
            "consumable_by_runtime": True,
            "default_transferable": False,
            "kind": "runtime_consumable_scoped_ticket",
        },
        "integrity": {
            "profile": "sclite-v0.3-scoped-ticket-integrity",
            "ticket_binds_execution_contract_digest": contract_digest,
        },
        "signature": {
            "identity_signature_required": False,
            "mode": "not_signed_integrity_only_fixture",
            "note": "RExecOp scoped ticket; signer trust remains external.",
        },
        "non_claims": [
            "does_not_prove_legal_authorization",
            "does_not_prove_signer_identity",
            "does_not_prove_runtime_enforcement",
            "does_not_prove_live_vulnerability_evidence",
        ],
    }
    return _validate_ticket(artifact)


def build_scoped_execution_receipt(
    operation: Any,
    plan: Any,
    execution_contract: dict[str, Any],
    execution_ticket: dict[str, Any],
    *,
    completed_at: str | None,
    rexecop_mode: Any,
    execution_plan_steps: Any,
    link: Any,
    validate: Any,
) -> dict[str, Any]:
    ended_at = completed_at or operation.updated_at
    capability_mode = rexecop_mode(plan.mode)
    steps = execution_plan_steps(plan)
    ticket_id = str(execution_ticket["ticket_id"])
    receipt_id = f"scoped-ticket-receipt-{operation.id}"
    artifact = {
        "artifact_type": "execution_receipt",
        "schema_version": "v0.2",
        "schema_ref": SCLITE_SCHEMA_REFS["execution_receipt"],
        "receipt_id": receipt_id,
        "created_at": ended_at,
        "links": {
            "execution_contract": link("execution_contract", execution_contract),
            "execution_ticket": link("execution_ticket", execution_ticket),
        },
        "runtime": {
            "name": "rexecop-executor",
            "runtime_ref": REXECOP_RUNTIME_REF,
            "version": "phase-3c",
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
            "status": "dry_run" if capability_mode == "dry_run" else operation.state,
            "returncode": 0,
            "summary": f"RExecOp scoped-ticket receipt for operation {operation.id}",
            "stderr_present": False,
            "stdout_present": False,
        },
        "ticket_use": {
            "ticket_id": ticket_id,
            "consumed_by_runtime": REXECOP_RUNTIME_REF,
            "one_shot_consumed": True,
            "use_count": 1,
        },
        "evidence_refs": [
            {"kind": "evidence_contract", "path": "06_evidence_contract.json"},
        ],
        "non_claims": [
            "receipt_does_not_include_raw_logs",
            "receipt_does_not_claim_live_target_execution",
            "receipt_does_not_prove_runtime_enforcement",
        ],
    }
    return validate("execution_receipt", artifact)


def build_receipt_bounded_evidence_contract(
    operation: Any,
    execution_receipt: dict[str, Any],
    execution_ticket: dict[str, Any],
    *,
    link: Any,
    validate: Any,
) -> dict[str, Any]:
    receipt_id = str(execution_receipt["receipt_id"])
    artifact = {
        "artifact_type": "evidence_contract",
        "schema_version": "v0.2",
        "schema_ref": SCLITE_SCHEMA_REFS["evidence_contract"],
        "evidence_contract_id": f"scoped-ticket-evidence-{operation.id}",
        "created_at": operation.updated_at,
        "links": {
            "execution_receipt": link("execution_receipt", execution_receipt),
            "execution_ticket": link("execution_ticket", execution_ticket),
        },
        "claims": [
            {
                "bounded_by_receipt": True,
                "claim_type": "receipt_bounded_dry_run",
                "id": "receipt_bound_dry_run",
                "requires_live_execution": False,
                "source_receipt_id": receipt_id,
                "statement": (
                    "The scoped ticket was used once by rexecop-executor and produced "
                    "only a dry-run receipt."
                ),
                "status": "met",
            }
        ],
        "non_claims": [
            "does_not_claim_live_vulnerability_evidence",
            "does_not_include_private_runtime_logs",
            "does_not_prove_legal_authorization",
            "does_not_prove_runtime_enforcement",
        ],
        "replay": {
            "mode": "static_bundle_verification",
            "live_execution_required": False,
        },
        "verification": {
            "commands": [
                "sclite verify-ticket-use 04_execution_ticket.json "
                "--contract 03_execution_contract.json "
                "--receipt 05_execution_receipt.json "
                "--evidence-contract 06_evidence_contract.json",
            ]
        },
    }
    return validate("evidence_contract", artifact)


def build_trust_profile_ref(
    execution_ticket: dict[str, Any],
    *,
    operation_id: str,
    created_at: str,
    link: Any,
) -> dict[str, Any]:
    digest = artifact_descriptor(execution_ticket)["digest"]
    return {
        "artifact_type": "trust_profile_ref",
        "schema_version": "v0.1",
        "schema_ref": "schemas/trust_profile_ref.v0.1.schema.json",
        "profile_ref_id": f"rexecop-trust-ref-{operation_id}",
        "created_at": created_at,
        "trust_profile": "digest_only",
        "links": {"subject": link("execution_ticket", execution_ticket)},
        "reference": {
            "kind": "local_digest_reference",
            "digest": digest,
        },
        "integrity": {
            "binding_mode": "subject_descriptor_digest",
            "subject_artifact_digest": digest,
        },
        "verification_boundary": {
            "external_verifier_decides_trust": True,
            "sclite_validates_digest_binding_only": True,
        },
        "non_claims": [
            "does_not_prove_signer_identity",
            "does_not_decide_trust",
            "does_not_verify_revocation",
        ],
    }


def build_carrier_profile_ref(
    execution_ticket: dict[str, Any],
    *,
    operation_id: str,
    bundle_dir: str,
    created_at: str,
    link: Any,
) -> dict[str, Any]:
    digest = artifact_descriptor(execution_ticket)["digest"]
    return {
        "artifact_type": "carrier_profile_ref",
        "schema_version": "v0.1",
        "schema_ref": "schemas/carrier_profile_ref.v0.1.schema.json",
        "profile_ref_id": f"rexecop-carrier-ref-{operation_id}",
        "created_at": created_at,
        "carrier_profile": "tecrax_review_bundle",
        "links": {"subject": link("execution_ticket", execution_ticket)},
        "reference": {
            "kind": "local_review_bundle",
            "media_type": "application/vnd.sclite.review-bundle+json",
            "uri": bundle_dir,
        },
        "integrity": {
            "binding_mode": "subject_descriptor_digest",
            "subject_artifact_digest": digest,
        },
        "transport_boundary": {
            "external_carrier_delivers_payload": True,
            "sclite_validates_digest_binding_only": True,
        },
        "non_claims": [
            "does_not_prove_carrier_delivery",
            "does_not_implement_transport_adapter",
            "does_not_authorize_execution",
        ],
    }


def write_full_bundle_sidecars(
    bundle_dir: str | Path,
    *,
    operation_id: str,
    created_at: str,
    execution_ticket: dict[str, Any],
    link: Any,
) -> dict[str, dict[str, Any]]:
    base = Path(bundle_dir)
    sidecars = {
        TRUST_PROFILE_REF_FILE: build_trust_profile_ref(
            execution_ticket,
            operation_id=operation_id,
            created_at=created_at,
            link=link,
        ),
        CARRIER_PROFILE_REF_FILE: build_carrier_profile_ref(
            execution_ticket,
            operation_id=operation_id,
            bundle_dir=str(base.resolve()),
            created_at=created_at,
            link=link,
        ),
    }
    for filename, payload in sidecars.items():
        path = base / filename
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return sidecars


def write_kernel_guard_manifest(bundle_dir: str | Path) -> dict[str, Any]:
    base = Path(bundle_dir)
    manifest_path = base / "artifact_chain_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    guard = build_kernel_guard_manifest(
        manifest,
        key=REXECOP_FIXTURE_GUARD_KEY,
        key_id=REXECOP_FIXTURE_GUARD_KEY_ID,
        nonces=[f"nonce-{index}" for index, _entry in enumerate(manifest["entries"])],
    )
    path = base / KERNEL_GUARD_MANIFEST_FILE
    path.write_text(json.dumps(guard, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return guard


def verify_full_bundle(
    bundle_dir: str | Path,
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    verify_ticket_use(
        artifacts["execution_ticket"],
        artifacts["execution_contract"],
        artifacts["execution_receipt"],
        artifacts["evidence_contract"],
        strict_ticket_profile=True,
    )
    review_record = review_bundle(bundle_dir)
    if review_record.get("verdict") != "pass":
        raise ValueError(
            f"full bundle review expected pass, got {review_record.get('verdict')!r}"
        )
    return review_record
