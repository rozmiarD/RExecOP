from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from govengine import (
    admit_typed_execution,
    explain_typed_execution_governance,
    project_typed_execution_policy_overlay,
    typed_execution_admission_digest,
    typed_execution_control_catalog,
)
from govengine.typed_execution_governance import (
    evaluate_typed_execution_stack_compatibility as govengine_stack_compatibility,
)
from govengine.typed_execution_governance import (
    network_policy_binding_digest,
    runtime_capability_descriptor_digest,
)

from rexecop.connectors.errors import READ_ONLY_MODES
from rexecop.errors import RExecOpValidationError
from rexecop.execution.typed_spec import (
    COMMAND_EXECUTION_SPEC_SCHEMA,
    HTTP_ACTION_EXECUTION_SPEC_SCHEMA,
    STATIC_FIXTURE_EXECUTION_SPEC_SCHEMA,
)

TYPED_EXECUTION_GOVERNANCE_BUNDLE_SCHEMA = "rexecop.typed_execution_governance_bundle.v0.1"
TYPED_EXECUTION_STACK_COMPATIBILITY_SCHEMA = "rexecop.typed_execution_stack_compatibility.v0.1"

EXPECTED_TYPED_EXECUTION_CONTROLS = (
    "backend_class_supported",
    "no_raw_shell",
    "read_only_posture",
    "capability_descriptor_digest_present",
    "step_execution_spec_digest_present",
    "payload_digest_present",
    "receipt_required",
    "output_digest_required",
    "network_boundary_match",
    "network_destination_binding_match",
    "secret_ref_requirements_met",
    "mutation_requires_approval",
)


def build_typed_execution_stack_compatibility_request(
    *,
    request_id: str = "rexecop-typed-execution-stack",
) -> dict[str, Any]:
    from rexecop.connectors.registry import list_connector_backend_descriptors

    return {
        "schema_version": "v0.1",
        "request_id": request_id,
        "backend_descriptors": [item.as_dict() for item in list_connector_backend_descriptors()],
        "required_controls": list(EXPECTED_TYPED_EXECUTION_CONTROLS),
    }


def evaluate_typed_execution_stack_compatibility(
    *,
    request_id: str = "rexecop-typed-execution-stack",
) -> dict[str, Any]:
    request = build_typed_execution_stack_compatibility_request(request_id=request_id)
    report = govengine_stack_compatibility(request)
    catalog = typed_execution_control_catalog()
    payload = report.as_dict()
    return {
        "schema": TYPED_EXECUTION_STACK_COMPATIBILITY_SCHEMA,
        "status": payload["status"],
        "request_id": payload["request_id"],
        "report_digest": payload["report_digest"],
        "supported_backends": payload["supported_backends"],
        "unsupported_backends": payload["unsupported_backends"],
        "missing_controls": payload["missing_controls"],
        "blockers": payload["blockers"],
        "govengine_control_catalog": catalog,
        "compatibility": payload,
        "non_claims": list(payload["non_claims"]),
    }


def typed_execution_governance_overlay(operation: Mapping[str, Any]) -> dict[str, Any]:
    """Project policy controls without manufacturing approval evidence."""
    metadata = operation.get("metadata")
    if not isinstance(metadata, Mapping):
        metadata = operation if isinstance(operation, Mapping) else {}
    overlay: dict[str, Any] = {"evidence_requirements": {"receipt_required": True}}
    record = metadata.get("policy_enforcement")
    if isinstance(record, Mapping):
        plan = record.get("plan")
        if isinstance(plan, Mapping):
            controls = plan.get("controls")
            if isinstance(controls, Mapping):
                policy_overlay = project_typed_execution_policy_overlay(controls)
                _merge_typed_execution_policy_overlay(overlay, policy_overlay)
    return overlay


def build_typed_execution_governance_request(
    *,
    spec: Mapping[str, Any],
    operation_id: str,
    mode: str,
    shared_state: Mapping[str, Any] | None = None,
    evidence_requirements: Mapping[str, Any] | None = None,
    allowed_network_egress: list[str] | None = None,
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
    policy_egress = allowed_network_egress or overlay.get("allowed_network_egress")
    network_policy_raw = spec.get("network_policy_binding")
    network_policy = dict(network_policy_raw) if isinstance(network_policy_raw, Mapping) else {}
    if policy_egress and network_policy:
        requested_egress = [str(item) for item in policy_egress]
        profile_egress = network_policy.get("allowed_network_egress")
        if isinstance(profile_egress, list):
            network_policy["allowed_network_egress"] = [
                item for item in profile_egress if item in requested_egress
            ]
    egress = network_policy.get("allowed_network_egress")
    if not isinstance(egress, list):
        egress = [str(item) for item in policy_egress] if policy_egress else []
    if isinstance(spec.get("required_capability_descriptors"), list):
        required = list(spec["required_capability_descriptors"])
    else:
        required = []
    request_metadata: dict[str, Any] = {}
    allowed_backends = overlay.get("allowed_backend_classes")
    if isinstance(allowed_backends, list) and allowed_backends:
        request_metadata["allowed_backend_classes"] = list(allowed_backends)
    payload = spec.get("payload")
    destination = payload.get("destination_binding") if isinstance(payload, Mapping) else None
    destination_fields: dict[str, Any] = {}
    if isinstance(destination, Mapping):
        destination_fields = {
            "destination_binding": dict(destination),
        }
        request_metadata["require_destination_binding"] = True
    request_id = str(overlay.get("request_id") or "").strip() or (
        f"rexecop-typed-execution:{operation_id}:{step_id}"
    )
    capability_projection = _runtime_capability_projection(capability)
    return {
        "schema_version": "v0.1",
        "request_id": request_id,
        "operation_id": operation_id,
        "step_id": step_id,
        "operation_mode": mode,
        "step_execution_spec_digest": str(spec.get("digest") or "").strip(),
        "capability_descriptor_digest": runtime_capability_descriptor_digest(capability_projection),
        "payload_schema": str(spec.get("payload_schema") or "").strip(),
        "payload_digest": _payload_digest(spec),
        "backend_class": str(spec.get("backend_class") or "").strip(),
        "connector": str(spec.get("connector") or "").strip(),
        "action": str(spec.get("action") or "").strip(),
        "read_only": bool(spec.get("read_only")),
        "side_effect_class": _side_effect_class(spec),
        "capability_descriptor": capability_projection,
        "evidence_requirements": evidence,
        "allowed_network_egress": list(egress),
        "network_policy_binding": network_policy,
        "network_policy_binding_digest": (
            network_policy_binding_digest(network_policy) if network_policy else ""
        ),
        "required_capability_descriptors": list(required),
        "metadata": request_metadata,
        **destination_fields,
    }


def evaluate_typed_execution_governance(
    *,
    spec: Mapping[str, Any],
    operation_id: str,
    mode: str,
    shared_state: Mapping[str, Any] | None = None,
    evidence_requirements: Mapping[str, Any] | None = None,
    allowed_network_egress: list[str] | None = None,
) -> dict[str, Any]:
    request = build_typed_execution_governance_request(
        spec=spec,
        operation_id=operation_id,
        mode=mode,
        shared_state=shared_state,
        evidence_requirements=evidence_requirements,
        allowed_network_egress=allowed_network_egress,
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
) -> dict[str, Any]:
    """Fail-closed typed execution admission before connector backend IO."""
    request = build_typed_execution_governance_request(
        spec=spec,
        operation_id=operation_id,
        mode=mode,
        shared_state=shared_state,
        evidence_requirements=evidence_requirements,
        allowed_network_egress=allowed_network_egress,
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
        "admission_digest": typed_execution_admission_digest(admission),
        "request_digest": str(payload.get("subject_ref") or ""),
    }
    specs = shared_state.get("typed_execution_specs")
    if isinstance(specs, dict):
        binding = specs.get(step_id)
        if isinstance(binding, dict):
            binding["admission_digest"] = admissions[step_id]["admission_digest"]
            binding["governance_request_digest"] = admissions[step_id]["request_digest"]
    return admissions[step_id]


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
        "secret_ref_requirements": [dict(item) for item in secret_refs if isinstance(item, Mapping)]
        if isinstance(secret_refs, list)
        else [],
        "declared_capability_descriptors": list(declared) if isinstance(declared, list) else [],
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


def _merge_typed_execution_policy_overlay(
    overlay: dict[str, Any],
    policy_overlay: Mapping[str, Any],
) -> None:
    evidence = overlay.setdefault("evidence_requirements", {})
    policy_evidence = policy_overlay.get("evidence_requirements")
    if isinstance(policy_evidence, Mapping):
        evidence.update(dict(policy_evidence))
    if evidence.pop("output_digest_required", None):
        overlay["output_digest_required"] = True
    if policy_overlay.get("output_digest_required"):
        overlay["output_digest_required"] = True
    for key in (
        "allowed_network_egress",
        "allowed_backend_classes",
        "policy_control_ids",
        "typed_execution_control_ids",
        "read_only_required",
        "no_raw_shell",
    ):
        value = policy_overlay.get(key)
        if value:
            overlay[key] = value
