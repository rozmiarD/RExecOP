from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from string import hexdigits
from typing import Any

from rexecop.errors import RExecOpValidationError
from rexecop.execution.bounded_subprocess import validate_output_limit

EXECUTION_REQUEST_SCHEMA_VERSION = "v0.2"
EXECUTION_RECEIPT_SCHEMA_VERSION = "v0.2"
TYPED_EXECUTION_BINDING_SCHEMA = "rexecop.typed_execution_binding.v0.1"


@dataclass(frozen=True)
class ResourceLimits:
    timeout_seconds: float = 0.0
    max_steps: int = 0
    max_output_bytes: int = 65536

    def __post_init__(self) -> None:
        validated_max_output_bytes(self.max_output_bytes)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> ResourceLimits:
        raw = dict(value or {})
        timeout = float(raw.get("timeout_seconds") or 0.0)
        max_steps = int(raw.get("max_steps") or 0)
        max_output_bytes = validated_max_output_bytes(
            raw["max_output_bytes"] if "max_output_bytes" in raw else 65536
        )
        if timeout < 0 or max_steps < 0 or max_output_bytes < 1:
            raise RExecOpValidationError("invalid execution resource limits")
        return cls(
            timeout_seconds=timeout,
            max_steps=max_steps,
            max_output_bytes=max_output_bytes,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def validated_max_output_bytes(value: object) -> int:
    try:
        return validate_output_limit(value)
    except ValueError as exc:
        raise RExecOpValidationError("invalid execution resource limits") from exc


@dataclass(frozen=True)
class ExecutionPolicyBinding:
    schema_version: str = ""
    enforcement_plan_id: str = ""
    enforcement_plan_digest: str = ""
    admission_id: str = ""
    admission_digest: str = ""
    policy_pack_id: str = ""
    policy_pack_version: str = ""
    policy_pack_digest: str = ""
    verdict_id: str = ""
    verdict_digest: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> ExecutionPolicyBinding:
        raw = dict(value or {})
        item = cls(
            schema_version=str(raw.get("schema_version") or "").strip(),
            enforcement_plan_id=str(raw.get("enforcement_plan_id") or "").strip(),
            enforcement_plan_digest=str(raw.get("enforcement_plan_digest") or "").strip(),
            admission_id=str(raw.get("admission_id") or "").strip(),
            admission_digest=str(raw.get("admission_digest") or "").strip(),
            policy_pack_id=str(raw.get("policy_pack_id") or "").strip(),
            policy_pack_version=str(raw.get("policy_pack_version") or "").strip(),
            policy_pack_digest=str(raw.get("policy_pack_digest") or "").strip(),
            verdict_id=str(raw.get("verdict_id") or "").strip(),
            verdict_digest=str(raw.get("verdict_digest") or "").strip(),
        )
        item.validate()
        return item

    @property
    def present(self) -> bool:
        return any(asdict(self).values())

    def validate(self) -> None:
        if not self.present:
            return
        for name, value in asdict(self).items():
            if not value:
                raise RExecOpValidationError(f"incomplete execution policy binding: {name}")
        for digest in (
            self.enforcement_plan_digest,
            self.admission_digest,
            self.policy_pack_digest,
            self.verdict_digest,
        ):
            if not _is_sha256_reference(digest):
                raise RExecOpValidationError("invalid execution policy digest")

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionStep:
    step_id: str
    step_type: str
    action: str
    connector: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ExecutionStep:
        step_id = str(value.get("id") or value.get("step_id") or "").strip()
        if not step_id:
            raise RExecOpValidationError("execution step missing id")
        return cls(
            step_id=step_id,
            step_type=str(value.get("type") or "internal").strip() or "internal",
            action=str(value.get("action") or "").strip(),
            connector=str(value.get("connector") or "").strip(),
            metadata=_public_metadata(value),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_type": self.step_type,
            "action": self.action,
            "connector": self.connector,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ExecutionRequest:
    request_id: str
    operation_id: str
    target_ref: str
    mode: str
    source: str = "approved_workflow_plan"
    schema_version: str = EXECUTION_REQUEST_SCHEMA_VERSION
    steps: tuple[ExecutionStep, ...] = field(default_factory=tuple)
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    policy_binding: ExecutionPolicyBinding = field(default_factory=ExecutionPolicyBinding)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.request_id:
            raise RExecOpValidationError("execution request missing id")
        if not self.operation_id:
            raise RExecOpValidationError("execution request missing operation id")
        if not self.target_ref:
            raise RExecOpValidationError("execution request missing target")
        if self.schema_version != EXECUTION_REQUEST_SCHEMA_VERSION:
            raise RExecOpValidationError("unsupported execution request schema")
        self.policy_binding.validate()

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "operation_id": self.operation_id,
            "target_ref": self.target_ref,
            "mode": self.mode,
            "source": self.source,
            "schema_version": self.schema_version,
            "steps": [step.as_dict() for step in self.steps],
            "resource_limits": self.resource_limits.as_dict(),
            "policy_binding": self.policy_binding.as_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ExecutionStepReceipt:
    step_id: str
    success: bool
    error_class: str = ""
    output_digest_refs: Mapping[str, str] = field(default_factory=dict)
    output_truncated: Mapping[str, bool] = field(default_factory=dict)
    execution_spec_digest: str = ""
    capability_descriptor_digest: str = ""
    planned_destination_binding: Mapping[str, Any] = field(default_factory=dict)
    observed_destination_binding: Mapping[str, Any] = field(default_factory=dict)
    runtime_receipt_binding: Mapping[str, Any] = field(default_factory=dict)
    receipt_conformance: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "step_id": self.step_id,
            "success": self.success,
            "error_class": self.error_class,
            "output_digest_refs": dict(self.output_digest_refs),
            "output_truncated": dict(self.output_truncated),
        }
        if self.execution_spec_digest:
            payload["execution_spec_digest"] = self.execution_spec_digest
        if self.capability_descriptor_digest:
            payload["capability_descriptor_digest"] = self.capability_descriptor_digest
        if self.planned_destination_binding:
            payload["planned_destination_binding"] = dict(self.planned_destination_binding)
        if self.observed_destination_binding:
            payload["observed_destination_binding"] = dict(self.observed_destination_binding)
        if self.runtime_receipt_binding:
            payload["runtime_receipt_binding"] = dict(self.runtime_receipt_binding)
        if self.receipt_conformance:
            payload["receipt_conformance"] = dict(self.receipt_conformance)
        return payload


@dataclass(frozen=True)
class ExecutionReceipt:
    receipt_id: str
    request_id: str
    request_digest: str
    operation_id: str
    success: bool
    schema_version: str = EXECUTION_RECEIPT_SCHEMA_VERSION
    receipt_digest: str = ""
    policy_binding: ExecutionPolicyBinding = field(default_factory=ExecutionPolicyBinding)
    enforcement: Mapping[str, Any] = field(default_factory=dict)
    executed_steps: tuple[str, ...] = field(default_factory=tuple)
    step_receipts: tuple[ExecutionStepReceipt, ...] = field(default_factory=tuple)
    typed_execution_binding: Mapping[str, Any] = field(default_factory=dict)
    governance_bindings: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""
    error_class: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "receipt_id": self.receipt_id,
            "request_id": self.request_id,
            "request_digest": self.request_digest,
            "operation_id": self.operation_id,
            "schema_version": self.schema_version,
            "receipt_digest": self.receipt_digest,
            "policy_binding": self.policy_binding.as_dict(),
            "enforcement": dict(self.enforcement),
            "success": self.success,
            "executed_steps": list(self.executed_steps),
            "step_receipts": [step.as_dict() for step in self.step_receipts],
            "error": self.error,
            "error_class": self.error_class,
        }
        if self.typed_execution_binding:
            payload["typed_execution_binding"] = dict(self.typed_execution_binding)
        if self.governance_bindings:
            payload["governance_bindings"] = dict(self.governance_bindings)
        return payload


def execution_request_from_workflow(
    *,
    operation_id: str,
    target: str,
    mode: str,
    planned_steps: list[dict[str, Any]],
    max_steps: int | None = None,
    max_output_bytes: int = 65536,
    timeout_seconds: float = 0.0,
    policy_binding: Mapping[str, Any] | None = None,
) -> ExecutionRequest:
    return ExecutionRequest(
        request_id=f"exec-request:{operation_id}",
        operation_id=operation_id,
        target_ref=target,
        mode=mode,
        steps=tuple(ExecutionStep.from_mapping(step) for step in planned_steps),
        resource_limits=ResourceLimits(
            timeout_seconds=timeout_seconds,
            max_steps=max_steps or len(planned_steps),
            max_output_bytes=max_output_bytes,
        ),
        policy_binding=ExecutionPolicyBinding.from_mapping(policy_binding),
    )


def execution_request_digest(request: ExecutionRequest) -> str:
    return _record_digest(request.as_dict())


def execution_receipt_digest(receipt: ExecutionReceipt) -> str:
    payload = receipt.as_dict()
    payload["receipt_digest"] = ""
    return _record_digest(payload)


def execution_receipt_from_results(
    *,
    request: ExecutionRequest,
    success: bool,
    executed_steps: list[str],
    step_results: Mapping[str, Mapping[str, Any]],
    typed_execution_specs: Mapping[str, Any] | None = None,
    output_digest_required: bool = False,
    error: str = "",
    error_class: str = "",
) -> ExecutionReceipt:
    step_receipts = tuple(
        _step_receipt(
            step_id,
            result,
            typed_execution_specs=typed_execution_specs,
        )
        for step_id, result in step_results.items()
    )
    digests_present = all(
        bool(step.output_digest_refs) for step in step_receipts if step.step_id in executed_steps
    )
    if output_digest_required and not digests_present:
        raise RExecOpValidationError("required execution output digest missing")
    typed_binding = build_typed_execution_binding(
        executed_steps,
        typed_execution_specs,
    )
    governance_bindings = _execution_governance_bindings(
        step_receipts,
        typed_execution_specs=typed_execution_specs,
    )
    enforcement_status = "enforced" if request.policy_binding.present else "not_required"
    receipt = ExecutionReceipt(
        receipt_id=f"exec-receipt:{request.operation_id}",
        request_id=request.request_id,
        request_digest=execution_request_digest(request),
        operation_id=request.operation_id,
        success=success,
        policy_binding=request.policy_binding,
        enforcement={
            "status": enforcement_status,
            "receipt_emitted": True,
            "output_digests_verified": digests_present,
            "typed_execution_specs_bound": bool(typed_binding.get("step_digests")),
            "resource_limits": request.resource_limits.as_dict(),
        },
        executed_steps=tuple(executed_steps),
        step_receipts=step_receipts,
        typed_execution_binding=typed_binding,
        governance_bindings=governance_bindings,
        error=error,
        error_class=error_class,
    )
    return replace(receipt, receipt_digest=execution_receipt_digest(receipt))


def build_typed_execution_binding(
    executed_steps: list[str] | tuple[str, ...],
    typed_execution_specs: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(typed_execution_specs, Mapping):
        return {}
    step_digests: dict[str, dict[str, str]] = {}
    for step_id in executed_steps:
        entry = typed_execution_specs.get(step_id)
        if not isinstance(entry, Mapping):
            continue
        spec_digest = str(entry.get("digest") or "").strip()
        capability_digest = str(entry.get("capability_descriptor_digest") or "").strip()
        if not spec_digest and not capability_digest:
            continue
        step_digests[str(step_id)] = {
            "execution_spec_digest": spec_digest,
            "capability_descriptor_digest": capability_digest,
            "schema": str(entry.get("schema") or "").strip(),
        }
        destination = entry.get("destination_binding")
        if isinstance(destination, Mapping):
            step_digests[str(step_id)]["origin_binding_digest"] = str(
                destination.get("origin_binding_digest") or ""
            )
        for source_key in ("admission_digest", "governance_request_digest"):
            value = str(entry.get(source_key) or "").strip()
            if value:
                step_digests[str(step_id)][source_key] = value
    if not step_digests:
        return {}
    binding = {
        "schema": TYPED_EXECUTION_BINDING_SCHEMA,
        "step_digests": step_digests,
        "non_claims": [
            "Runtime projection only; not a SCLite truth artifact.",
            "Binds digests only; does not embed typed execution payloads.",
        ],
    }
    binding["binding_digest"] = _record_digest(
        {
            "schema": binding["schema"],
            "step_digests": step_digests,
        }
    )
    return binding


def _execution_governance_bindings(
    step_receipts: tuple[ExecutionStepReceipt, ...],
    *,
    typed_execution_specs: Mapping[str, Any] | None,
) -> dict[str, Any]:
    bindings: dict[str, Any] = {}
    for step in step_receipts:
        if not step.runtime_receipt_binding or not step.receipt_conformance:
            continue
        item: dict[str, Any] = {
            "runtime_receipt_binding": dict(step.runtime_receipt_binding),
            "receipt_conformance": dict(step.receipt_conformance),
        }
        raw_spec = (
            typed_execution_specs.get(step.step_id)
            if isinstance(typed_execution_specs, Mapping)
            else None
        )
        governed = (
            raw_spec.get("governed_admission_binding") if isinstance(raw_spec, Mapping) else None
        )
        if governed is not None:
            item["governed_admission_binding"] = _bounded_governed_binding(governed)
        bindings[step.step_id] = item
    return bindings


def _bounded_governed_binding(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise RExecOpValidationError("invalid governed admission receipt binding")
    expected_fields = {
        "schema",
        "actual_operation_mode",
        "composite_admission_digest",
        "governance_request_digest",
        "governance_decision_digest",
        "approval_attestation_digest",
        "decision_expires_at",
    }
    if set(value) != expected_fields:
        raise RExecOpValidationError("invalid governed admission receipt binding")
    result = {key: str(value.get(key) or "") for key in sorted(expected_fields)}
    if result["schema"] != "rexecop.governed_admission_binding.v0.1":
        raise RExecOpValidationError("unsupported governed admission receipt binding")
    if result["actual_operation_mode"] not in {"apply", "recovery"}:
        raise RExecOpValidationError("invalid governed admission receipt mode")
    for key in (
        "composite_admission_digest",
        "governance_request_digest",
        "governance_decision_digest",
        "approval_attestation_digest",
    ):
        if not _is_sha256_reference(result[key]):
            raise RExecOpValidationError("invalid governed admission receipt digest")
    if not result["decision_expires_at"]:
        raise RExecOpValidationError("missing governed admission receipt expiry")
    return result


def _step_receipt(
    step_id: str,
    result: Mapping[str, Any],
    *,
    typed_execution_specs: Mapping[str, Any] | None = None,
) -> ExecutionStepReceipt:
    output = result.get("output")
    output_data = output if isinstance(output, Mapping) else {}
    data = output_data.get("data")
    response_data = data if isinstance(data, Mapping) else {}
    digests: dict[str, str] = {}
    for source in (response_data.get("output_digests"), output_data.get("output_digests")):
        if isinstance(source, Mapping):
            digests.update({str(key): str(value) for key, value in source.items()})
    if not digests:
        digests["record"] = _record_digest(output_data)
    truncated: dict[str, bool] = {}
    for source in (response_data.get("output_truncated"), output_data.get("output_truncated")):
        if isinstance(source, Mapping):
            truncated.update({str(key): bool(value) for key, value in source.items()})
    execution_spec_digest = ""
    capability_descriptor_digest = ""
    planned_destination: Mapping[str, Any] = {}
    if isinstance(typed_execution_specs, Mapping):
        entry = typed_execution_specs.get(step_id)
        if isinstance(entry, Mapping):
            execution_spec_digest = str(entry.get("digest") or "").strip()
            capability_descriptor_digest = str(
                entry.get("capability_descriptor_digest") or ""
            ).strip()
            destination = entry.get("destination_binding")
            if isinstance(destination, Mapping):
                planned_destination = dict(destination)
    observed_destination = response_data.get("observed_destination_binding")
    if not isinstance(observed_destination, Mapping):
        observed_destination = {}
    if planned_destination and observed_destination:
        planned_digest = str(planned_destination.get("origin_binding_digest") or "")
        observed_digest = str(observed_destination.get("origin_binding_digest") or "")
        if planned_digest != observed_digest:
            raise RExecOpValidationError("observed destination binding drift")
    runtime_receipt_binding = result.get("runtime_receipt_binding")
    if not isinstance(runtime_receipt_binding, Mapping):
        runtime_receipt_binding = {}
    receipt_conformance = result.get("receipt_conformance")
    if not isinstance(receipt_conformance, Mapping):
        receipt_conformance = {}
    return ExecutionStepReceipt(
        step_id=step_id,
        success=bool(result.get("success")),
        error_class=str(output_data.get("error_class") or response_data.get("error_class") or ""),
        output_digest_refs=digests,
        output_truncated=truncated,
        execution_spec_digest=execution_spec_digest,
        capability_descriptor_digest=capability_descriptor_digest,
        planned_destination_binding=planned_destination,
        observed_destination_binding=dict(observed_destination),
        runtime_receipt_binding=dict(runtime_receipt_binding),
        receipt_conformance=dict(receipt_conformance),
    )


def _record_digest(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _public_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "declared_type": str(value.get("type") or ""),
        "declared_connector": str(value.get("connector") or ""),
    }


def _is_sha256_reference(value: str) -> bool:
    prefix, separator, digest = value.partition(":")
    return (
        separator == ":"
        and prefix == "sha256"
        and len(digest) == 64
        and all(char in hexdigits for char in digest)
    )
