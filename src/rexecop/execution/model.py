from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from string import hexdigits
from typing import Any

from rexecop.errors import RExecOpValidationError

EXECUTION_REQUEST_SCHEMA_VERSION = "v0.2"
EXECUTION_RECEIPT_SCHEMA_VERSION = "v0.2"


@dataclass(frozen=True)
class ResourceLimits:
    timeout_seconds: float = 0.0
    max_steps: int = 0
    max_output_bytes: int = 65536

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> ResourceLimits:
        raw = dict(value or {})
        timeout = float(raw.get("timeout_seconds") or 0.0)
        max_steps = int(raw.get("max_steps") or 0)
        max_output_bytes = int(raw.get("max_output_bytes") or 65536)
        if timeout < 0 or max_steps < 0 or max_output_bytes < 1:
            raise RExecOpValidationError("invalid execution resource limits")
        return cls(
            timeout_seconds=timeout,
            max_steps=max_steps,
            max_output_bytes=max_output_bytes,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


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
            enforcement_plan_digest=str(
                raw.get("enforcement_plan_digest") or ""
            ).strip(),
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "success": self.success,
            "error_class": self.error_class,
            "output_digest_refs": dict(self.output_digest_refs),
            "output_truncated": dict(self.output_truncated),
        }


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
    error: str = ""
    error_class: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
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
    output_digest_required: bool = False,
    error: str = "",
    error_class: str = "",
) -> ExecutionReceipt:
    step_receipts = tuple(
        _step_receipt(step_id, result)
        for step_id, result in step_results.items()
    )
    digests_present = all(
        bool(step.output_digest_refs)
        for step in step_receipts
        if step.step_id in executed_steps
    )
    if output_digest_required and not digests_present:
        raise RExecOpValidationError("required execution output digest missing")
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
            "resource_limits": request.resource_limits.as_dict(),
        },
        executed_steps=tuple(executed_steps),
        step_receipts=step_receipts,
        error=error,
        error_class=error_class,
    )
    return replace(receipt, receipt_digest=execution_receipt_digest(receipt))


def _step_receipt(step_id: str, result: Mapping[str, Any]) -> ExecutionStepReceipt:
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
    return ExecutionStepReceipt(
        step_id=step_id,
        success=bool(result.get("success")),
        error_class=str(
            output_data.get("error_class")
            or response_data.get("error_class")
            or ""
        ),
        output_digest_refs=digests,
        output_truncated=truncated,
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
