from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from govengine import (
    GovEvidenceClaim,
    GovEvidenceRequirement,
    project_governance_trace,
    qualify_evidence_claim,
)
from sclite.artifacts import artifact_sha256

from rexecop.environment.loader import load_environment
from rexecop.errors import RExecOpValidationError
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.operation.state import OperationState
from rexecop.policy.operation import build_operation_policy_request

TRUTH_PATH_PROJECTION_SCHEMA = "rexecop.truth_path_projection.v0.1"


def _normalize_digest(value: str) -> str:
    digest = str(value or "").strip()
    if not digest:
        return ""
    return digest if digest.startswith("sha256:") else f"sha256:{digest}"


def _artifact_digest(value: Mapping[str, Any]) -> str:
    return _normalize_digest(artifact_sha256(dict(value)))

_RECEIPT_STATUS_BY_OPERATION = {
    OperationState.COMPLETED.value: "dry-run",
    OperationState.FAILED.value: "failed",
    OperationState.BLOCKED.value: "blocked",
    OperationState.CANCELLED.value: "interrupted",
}


def project_truth_path(
    operation: Operation,
    plan: OperationPlan,
    *,
    child_operation: Operation | None = None,
) -> dict[str, Any]:
    """Project one rebuildable truth-path summary without becoming a truth store."""
    metadata = operation.metadata
    policy_verdict = metadata.get("policy_verdict")
    policy_enforcement = metadata.get("policy_enforcement")
    if not isinstance(policy_verdict, Mapping) or not isinstance(policy_enforcement, Mapping):
        raise RExecOpValidationError("operation policy bindings are incomplete")

    policy_request = _policy_request_for_operation(operation, plan)
    governance_trace = project_governance_trace(
        policy_request=policy_request,
        policy_verdict=policy_verdict,
        policy_enforcement=policy_enforcement,
        trace_id=f"truth-path:{operation.id}",
    ).as_dict()

    observation = _observation_summary(metadata)
    auto_reaction = _auto_reaction_summary(metadata)
    sclite_refs = _sclite_ref_summary(operation.sclite_refs)
    typed_execution = _typed_execution_summary(metadata)
    evidence_claims = _evidence_claim_summaries(
        operation=operation,
        governance_trace=governance_trace,
    )

    links = _truth_path_links(
        operation=operation,
        observation=observation,
        auto_reaction=auto_reaction,
        child_operation=child_operation,
        sclite_refs=sclite_refs,
    )

    return {
        "schema": TRUTH_PATH_PROJECTION_SCHEMA,
        "operation_id": operation.id,
        "subject_ref": governance_trace["subject_ref"],
        "operation": {
            "profile": operation.profile,
            "environment": operation.environment,
            "intent": operation.intent,
            "target": operation.target,
            "mode": operation.mode,
            "state": operation.state,
            "risk": plan.risk,
        },
        "governance_trace": governance_trace,
        "observation": observation,
        "auto_reaction": auto_reaction,
        "typed_execution": typed_execution,
        "sclite_refs": sclite_refs,
        "evidence_claims": evidence_claims,
        "links": links,
        "non_claims": [
            "Does not execute work or approve operators.",
            "Does not store or canonicalize SCLite artifacts.",
            "Does not reinterpret GovEngine policy reasoning beyond digest binding.",
            "Rebuildable from runtime state plus external SCLite refs.",
        ],
    }


def _policy_request_for_operation(operation: Operation, plan: OperationPlan) -> dict[str, object]:
    environment_path = str(operation.metadata.get("environment_path") or "").strip()
    if not environment_path:
        raise RExecOpValidationError("operation environment_path is required for truth path")
    environment = load_environment(Path(environment_path))
    return build_operation_policy_request(
        operation_id=operation.id,
        profile=operation.profile,
        environment=environment,
        intent=operation.intent,
        target=operation.target,
        mode=operation.mode,
        risk=str(plan.risk),
    )


def _observation_summary(metadata: Mapping[str, Any]) -> dict[str, Any]:
    shared_state = metadata.get("shared_state")
    if not isinstance(shared_state, Mapping):
        return {"status": "absent"}
    observation = shared_state.get("reaction_observation")
    if not isinstance(observation, Mapping):
        return {"status": "absent"}
    facts = observation.get("facts")
    facts_digest = ""
    if isinstance(facts, Mapping):
        facts_digest = _artifact_digest(dict(facts))
    source = observation.get("source")
    source_summary: dict[str, str] = {}
    if isinstance(source, Mapping):
        source_summary = {
            "operation_id": str(source.get("operation_id") or ""),
            "intent_id": str(source.get("intent_id") or ""),
            "target_id": str(source.get("target_id") or ""),
        }
    return {
        "status": "present",
        "artifact_type": str(observation.get("artifact_type") or ""),
        "schema_ref": str(observation.get("schema_ref") or ""),
        "observation_digest": _artifact_digest(dict(observation)),
        "facts_schema_ref": (
            str(facts.get("schema_ref") or "") if isinstance(facts, Mapping) else ""
        ),
        "facts_digest": facts_digest,
        "source": source_summary,
    }


def _auto_reaction_summary(metadata: Mapping[str, Any]) -> dict[str, Any]:
    auto_reaction = metadata.get("auto_reaction")
    if not isinstance(auto_reaction, Mapping):
        return {"status": "absent"}
    admission = auto_reaction.get("admission")
    admission_summary: dict[str, str] = {}
    if isinstance(admission, Mapping):
        admission_summary = {
            "status": str(admission.get("status") or ""),
            "decision": str(admission.get("decision") or ""),
            "decision_id": str(admission.get("decision_id") or ""),
        }
    automation_admission = auto_reaction.get("automation_admission")
    automation_summary: dict[str, str] = {}
    if isinstance(automation_admission, Mapping):
        automation_summary = {
            "status": str(automation_admission.get("status") or ""),
            "reason_code": str(automation_admission.get("reason_code") or ""),
            "admission_digest": str(automation_admission.get("admission_digest") or ""),
            "automation_chain_digest": str(
                automation_admission.get("automation_chain_digest")
                or auto_reaction.get("automation_chain_digest")
                or ""
            ),
        }
    return {
        "status": str(auto_reaction.get("status") or ""),
        "reaction_id": str(auto_reaction.get("reaction_id") or ""),
        "chain_root": _normalize_digest(str(auto_reaction.get("chain_root") or "")),
        "automation_chain_digest": _normalize_digest(
            str(auto_reaction.get("automation_chain_digest") or "")
        ),
        "outcome": str(auto_reaction.get("outcome") or ""),
        "rule_id": str(auto_reaction.get("rule_id") or ""),
        "rule_digest": str(auto_reaction.get("rule_digest") or ""),
        "child_operation_id": str(auto_reaction.get("child_operation_id") or ""),
        "admission": admission_summary,
        "automation_admission": automation_summary,
    }


def _sclite_ref_summary(refs: Mapping[str, Any]) -> dict[str, Any]:
    if not refs:
        return {"status": "absent", "artifacts": []}
    artifacts: list[dict[str, str]] = []
    for role, item in sorted(refs.items()):
        if not isinstance(item, Mapping):
            continue
        artifacts.append(
            {
                "role": role,
                "schema_ref": str(item.get("sclite_schema_ref") or ""),
                "digest": _normalize_digest(str(item.get("digest") or "")),
                "status": str(item.get("status") or ""),
            }
        )
    return {"status": "present", "artifacts": artifacts}


def _typed_execution_summary(metadata: Mapping[str, Any]) -> dict[str, Any]:
    shared_state = metadata.get("shared_state")
    if not isinstance(shared_state, Mapping):
        return {"status": "absent"}
    binding = shared_state.get("typed_execution_binding")
    if not isinstance(binding, Mapping):
        execution_receipt = shared_state.get("execution_receipt")
        if isinstance(execution_receipt, Mapping):
            return {
                "status": "partial",
                "execution_receipt_digest": _artifact_digest(dict(execution_receipt)),
            }
        return {"status": "absent"}
    return {
        "status": "present",
        "schema_version": str(binding.get("schema_version") or ""),
        "enforcement_plan_digest": str(binding.get("enforcement_plan_digest") or ""),
        "admission_digest": str(binding.get("admission_digest") or ""),
        "binding_digest": _artifact_digest(dict(binding)),
    }


def _evidence_claim_summaries(
    *,
    operation: Operation,
    governance_trace: Mapping[str, Any],
) -> list[dict[str, Any]]:
    receipt_status = _RECEIPT_STATUS_BY_OPERATION.get(operation.state, "dry-run")
    subject_ref = str(governance_trace.get("subject_ref") or "")
    admission_digest = str(governance_trace.get("admission_digest") or "")
    claims: list[tuple[str, str]] = [
        ("read_only_execution", "read-only execution completed within receipt bounds"),
        ("dry_run_execution", "dry-run execution completed within receipt bounds"),
    ]
    if operation.state == OperationState.BLOCKED.value:
        claims.append(("blocked_execution", "execution blocked before receipt emission"))
    if operation.metadata.get("manual_approval"):
        claims.append(
            ("approval_required_execution", "manual approval recorded for execution"),
        )

    requirements = {
        str(item.get("requirement_id") or ""): item
        for item in governance_trace.get("evidence_requirements") or []
        if isinstance(item, Mapping)
    }
    default_requirement = requirements.get("receipt_required")
    if default_requirement is None and requirements:
        default_requirement = next(iter(requirements.values()))
    if default_requirement is None:
        default_requirement = GovEvidenceRequirement(
            requirement_id="receipt_required",
            subject_ref=subject_ref,
            evidence_kind="execution_receipt",
            min_receipt_status="dry-run",
        ).as_dict()

    summaries: list[dict[str, Any]] = []
    for claim_id, statement in claims:
        claim = GovEvidenceClaim(
            claim_id=f"{operation.id}:{claim_id}",
            subject_ref=subject_ref,
            claim_type="execution_truth",
            statement=statement,
            receipt_refs=(f"receipt:{operation.id}",),
            metadata={
                "admission_digest": admission_digest,
                "operation_state": operation.state,
            },
        )
        qualification = qualify_evidence_claim(
            claim,
            default_requirement,
            receipt_status=receipt_status,
        )
        summaries.append(
            {
                "claim_id": claim.claim_id,
                "claim_type": claim_id,
                "result": qualification.result,
                "reason_code": qualification.reason_code,
                "receipt_status": qualification.receipt_status,
            }
        )
    return summaries


def _truth_path_links(
    *,
    operation: Operation,
    observation: Mapping[str, Any],
    auto_reaction: Mapping[str, Any],
    child_operation: Operation | None,
    sclite_refs: Mapping[str, Any],
) -> list[dict[str, str]]:
    enforcement = operation.metadata.get("policy_enforcement")
    admission_digest = ""
    if isinstance(enforcement, Mapping):
        admission_digest = str(enforcement.get("admission_digest") or "")
    links: list[dict[str, str]] = [
        {"kind": "operation", "ref": operation.id},
        {"kind": "governance_trace", "ref": admission_digest},
    ]
    if observation.get("status") == "present":
        links.append(
            {
                "kind": "observation_envelope",
                "ref": str(observation.get("observation_digest") or ""),
            }
        )
        facts_digest = str(observation.get("facts_digest") or "")
        if facts_digest:
            links.append({"kind": "diagnosis_facts", "ref": facts_digest})
    if auto_reaction.get("status") not in {"", "absent"}:
        links.append(
            {
                "kind": "reaction_chain",
                "ref": str(auto_reaction.get("chain_root") or ""),
            }
        )
        automation_chain_digest = str(auto_reaction.get("automation_chain_digest") or "")
        if automation_chain_digest:
            links.append(
                {
                    "kind": "automation_chain",
                    "ref": automation_chain_digest,
                }
            )
        child_id = str(auto_reaction.get("child_operation_id") or "")
        if child_id:
            links.append({"kind": "child_operation", "ref": child_id})
    if child_operation is not None:
        links.append({"kind": "child_operation", "ref": child_operation.id})
    for artifact in sclite_refs.get("artifacts") or []:
        if not isinstance(artifact, Mapping):
            continue
        digest = str(artifact.get("digest") or "").strip()
        if digest:
            links.append(
                {
                    "kind": f"sclite_{artifact.get('role')}",
                    "ref": digest,
                }
            )
    return links
