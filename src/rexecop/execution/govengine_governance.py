from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from govengine import admit_typed_execution, explain_typed_execution_governance

from rexecop.catalog.digest import canonical_digest
from rexecop.connectors.errors import READ_ONLY_MODES
from rexecop.errors import RExecOpValidationError
from rexecop.execution.typed_spec import (
    COMMAND_EXECUTION_SPEC_SCHEMA,
    HTTP_ACTION_EXECUTION_SPEC_SCHEMA,
    STATIC_FIXTURE_EXECUTION_SPEC_SCHEMA,
)

TYPED_EXECUTION_GOVERNANCE_BUNDLE_SCHEMA = "rexecop.typed_execution_governance_bundle.v0.1"


def typed_execution_governance_overlay(operation: Mapping[str, Any]) -> dict[str, Any]:
    """Seed shared_state overlay from operation-level admission and approvals."""
    metadata = operation.get("metadata")
    if not isinstance(metadata, Mapping):
        metadata = operation if isinstance(operation, Mapping) else {}
    evidence: dict[str, Any] = {"receipt_required": True}
    record = metadata.get("policy_enforcement")
    if isinstance(record, Mapping):
        digest = str(record.get("admission_digest") or "").strip()
        if digest.startswith("sha256:"):
            evidence["approval_evidence_ref"] = digest
    manual = metadata.get("manual_approval")
    if isinstance(manual, Mapping) and not evidence.get("approval_evidence_ref"):
        evidence["approval_evidence_ref"] = "sha256:" + canonical_digest(dict(manual))
    if not evidence.get("approval_evidence_ref"):
        admission = metadata.get("govengine_admission")
        if isinstance(admission, Mapping):
            evidence["approval_evidence_ref"] = "sha256:" + canonical_digest(
                {
                    "operation_id": str(operation.get("id") or metadata.get("operation_id") or ""),
                    "decision_type": str(admission.get("decision_type") or ""),
                    "summary": str(admission.get("summary") or ""),
                }
            )
    return {"evidence_requirements": evidence}


def build_typed_execution_governance_request(
    *,
    spec: Mapping[str, Any],
    operation_id: str,
    mode: str,
    shared_state: Mapping[str, Any] | None = None,
    evidence_requirements: Mapping[str, Any] | None = None,
    allowed_network_egress: list[str] | None = None,
    required_capability_descriptors: list[str] | None = None,
) -> dict[str, Any]:
    """Project one digest-bound typed execution spec into a GovEngine G5 request."""
    step_id = str(spec.get("step_id") or "").strip()
    if not step_id:
        raise RExecOpValidationError("typed execution governance missing step_id")
    capability = spec.get("capability_descriptor")
    if not isinstance(capability, Mapping):
        raise RExecOpValidationError("typed execution governance missing capability descriptor")
    overlay = _governance_overlay(shared_state)
    evidence = dict(overlay.get("evidence_requirements") or {})
    if evidence_requirements:
        evidence.update(dict(evidence_requirements))
    if "receipt_required" not in evidence:
        evidence["receipt_required"] = True
    if not evidence.get("approval_evidence_ref"):
        approval_ref = _approval_evidence_ref_from_shared_state(shared_state)
        if approval_ref:
            evidence["approval_evidence_ref"] = approval_ref
    egress = allowed_network_egress or overlay.get("allowed_network_egress")
    if not egress:
        egress = [_default_allowed_egress(capability)]
    required = required_capability_descriptors or overlay.get("required_capability_descriptors")
    if not required:
        declared = capability.get("declared_capability_descriptors")
        required = list(declared) if isinstance(declared, list) else []
    request_id = str(overlay.get("request_id") or "").strip() or (
        f"rexecop-typed-execution:{operation_id}:{step_id}"
    )
    return {
        "schema_version": "v0.1",
        "request_id": request_id,
        "operation_id": operation_id,
        "step_id": step_id,
        "operation_mode": mode,
        "step_execution_spec_digest": str(spec.get("digest") or "").strip(),
        "capability_descriptor_digest": str(capability.get("digest") or "").strip(),
        "payload_schema": str(spec.get("payload_schema") or "").strip(),
        "payload_digest": _payload_digest(spec),
        "backend_class": str(spec.get("backend_class") or "").strip(),
        "connector": str(spec.get("connector") or "").strip(),
        "action": str(spec.get("action") or "").strip(),
        "read_only": bool(spec.get("read_only")),
        "side_effect_class": _side_effect_class(spec),
        "capability_descriptor": _runtime_capability_projection(capability),
        "evidence_requirements": evidence,
        "allowed_network_egress": list(egress),
        "required_capability_descriptors": list(required),
    }


def evaluate_typed_execution_governance(
    *,
    spec: Mapping[str, Any],
    operation_id: str,
    mode: str,
    shared_state: Mapping[str, Any] | None = None,
    evidence_requirements: Mapping[str, Any] | None = None,
    allowed_network_egress: list[str] | None = None,
    required_capability_descriptors: list[str] | None = None,
) -> dict[str, Any]:
    request = build_typed_execution_governance_request(
        spec=spec,
        operation_id=operation_id,
        mode=mode,
        shared_state=shared_state,
        evidence_requirements=evidence_requirements,
        allowed_network_egress=allowed_network_egress,
        required_capability_descriptors=required_capability_descriptors,
    )
    bundle = explain_typed_execution_governance(request)
    payload = bundle.as_dict()
    return {
        "schema": TYPED_EXECUTION_GOVERNANCE_BUNDLE_SCHEMA,
        "status": payload["status"],
        "operation_id": operation_id,
        "step_id": payload["step_id"],
        "request_id": payload["request_id"],
        "bundle_digest": payload["bundle_digest"],
        "governance": payload["governance"],
        "compatibility": payload["compatibility"],
        "non_claims": list(payload["non_claims"]),
    }


def enforce_typed_execution_governance(
    *,
    spec: Mapping[str, Any],
    operation_id: str,
    mode: str,
    shared_state: dict[str, Any],
    evidence_requirements: Mapping[str, Any] | None = None,
    allowed_network_egress: list[str] | None = None,
    required_capability_descriptors: list[str] | None = None,
) -> dict[str, Any]:
    """Fail-closed typed execution admission before connector backend IO."""
    request = build_typed_execution_governance_request(
        spec=spec,
        operation_id=operation_id,
        mode=mode,
        shared_state=shared_state,
        evidence_requirements=evidence_requirements,
        allowed_network_egress=allowed_network_egress,
        required_capability_descriptors=required_capability_descriptors,
    )
    admission = admit_typed_execution(request)
    payload = admission.as_dict()
    step_id = str(spec.get("step_id") or "")
    admissions = shared_state.setdefault("typed_execution_admissions", {})
    admissions[step_id] = {
        "allowed": payload["allowed"],
        "outcome": payload["outcome"],
        "reason_code": payload["reason_code"],
        "blockers": list(payload.get("blockers") or []),
        "request_id": request["request_id"],
        "subject_ref": payload["subject_ref"],
        "signal": dict(payload.get("signal") or {}),
    }
    return admissions[step_id]


def _approval_evidence_ref_from_shared_state(
    shared_state: Mapping[str, Any] | None,
) -> str:
    if not isinstance(shared_state, Mapping):
        return ""
    execution_request = shared_state.get("execution_request")
    if isinstance(execution_request, Mapping):
        binding = execution_request.get("policy_binding")
        if isinstance(binding, Mapping):
            digest = str(binding.get("admission_digest") or "").strip()
            if digest.startswith("sha256:"):
                return digest
    manual = shared_state.get("manual_approval")
    if isinstance(manual, Mapping):
        return "sha256:" + canonical_digest(dict(manual))
    return ""


def _governance_overlay(shared_state: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(shared_state, Mapping):
        return {}
    overlay = shared_state.get("typed_execution_governance")
    return dict(overlay) if isinstance(overlay, Mapping) else {}


def _runtime_capability_projection(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    network_boundary = descriptor.get("network_boundary")
    secret_refs = descriptor.get("secret_ref_requirements")
    declared = descriptor.get("declared_capability_descriptors")
    return {
        "schema_version": "v0.1",
        "backend_class": str(descriptor.get("backend_class") or "").strip(),
        "identity_class": str(descriptor.get("identity_class") or "").strip(),
        "egress_class": str(descriptor.get("egress_class") or "").strip(),
        "read_only_backend": bool(descriptor.get("read_only_backend", False)),
        "live_backend_posture": str(descriptor.get("live_backend_posture") or "").strip(),
        "network_boundary": dict(network_boundary) if isinstance(network_boundary, Mapping) else {},
        "secret_ref_requirements": [
            dict(item) for item in secret_refs if isinstance(item, Mapping)
        ]
        if isinstance(secret_refs, list)
        else [],
        "declared_capability_descriptors": list(declared)
        if isinstance(declared, list)
        else [],
        "certification_tier": str(descriptor.get("certification_tier") or "").strip(),
        "mode": str(descriptor.get("mode") or "").strip(),
    }


def _payload_digest(spec: Mapping[str, Any]) -> str:
    payload = spec.get("payload")
    if not isinstance(payload, Mapping):
        raise RExecOpValidationError("typed execution governance missing payload")
    for key in ("shape_digest", "argv_digest", "action_digest"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    raise RExecOpValidationError("typed execution governance missing payload digest")


def _side_effect_class(spec: Mapping[str, Any]) -> str:
    payload = spec.get("payload")
    if not isinstance(payload, Mapping):
        return "read_only" if bool(spec.get("read_only")) else "mutation"
    schema = str(payload.get("schema") or "").strip()
    if schema in {
        STATIC_FIXTURE_EXECUTION_SPEC_SCHEMA,
        HTTP_ACTION_EXECUTION_SPEC_SCHEMA,
    }:
        return "mutation" if bool(payload.get("mutating")) else "read_only"
    if schema == COMMAND_EXECUTION_SPEC_SCHEMA:
        return "read_only"
    if str(spec.get("mode") or "") not in READ_ONLY_MODES and not bool(spec.get("read_only")):
        return "mutation"
    return "read_only"


def _default_allowed_egress(descriptor: Mapping[str, Any]) -> str:
    network_boundary = descriptor.get("network_boundary")
    if isinstance(network_boundary, Mapping):
        egress = str(network_boundary.get("egress") or "").strip()
        if egress:
            return egress
    return str(descriptor.get("egress_class") or "no_network").strip() or "no_network"