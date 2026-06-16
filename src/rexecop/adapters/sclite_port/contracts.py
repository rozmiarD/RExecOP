from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from rexecop.evidence.event import EvidenceEventType

PLACEHOLDER_EMITTER_NOTICE = (
    "Placeholder SCLite emitter is bootstrap/offline only. "
    "Receipt exports under .rexecop/receipts/ are non-authoritative summaries."
)

RECEIPT_EXPORT_AUTHORITY = "non_authoritative_export"

SCLITE_SCHEMA_REFS: dict[str, str] = {
    "intent_contract": "schemas/intent_contract.v0.2.schema.json",
    "policy_decision": "schemas/policy_decision.v0.2.schema.json",
    "execution_contract": "schemas/execution_contract.v0.2.schema.json",
    "execution_ticket": "schemas/execution_ticket.v0.2.schema.json",
    "execution_receipt": "schemas/execution_receipt.v0.2.schema.json",
    "evidence_contract": "schemas/evidence_contract.v0.2.schema.json",
}

ARTIFACT_SLOTS = (
    "intent_contract",
    "policy_decision",
    "execution_contract",
    "execution_ticket",
    "execution_receipt",
    "evidence_contract",
)

EVENT_SCLITE_MAPPING: dict[str, dict[str, str]] = {
    EvidenceEventType.OPERATION_CREATED.value: {
        "future_artifact": "intent_contract",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["intent_contract"],
    },
    EvidenceEventType.PLAN_GENERATED.value: {
        "future_artifact": "execution_contract",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["execution_contract"],
    },
    EvidenceEventType.GOVENGINE_DECISION_REQUESTED.value: {
        "future_artifact": "policy_decision",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["policy_decision"],
    },
    EvidenceEventType.GOVENGINE_DECISION_RECEIVED.value: {
        "future_artifact": "policy_decision",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["policy_decision"],
    },
    EvidenceEventType.APPROVAL_RECEIVED.value: {
        "future_artifact": "execution_ticket",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["execution_ticket"],
    },
    EvidenceEventType.STATE_TRANSITION.value: {
        "future_artifact": "execution_receipt",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["execution_receipt"],
    },
    EvidenceEventType.STEP_STARTED.value: {
        "future_artifact": "execution_receipt",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["execution_receipt"],
    },
    EvidenceEventType.STEP_COMPLETED.value: {
        "future_artifact": "execution_receipt",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["execution_receipt"],
    },
    EvidenceEventType.STEP_FAILED.value: {
        "future_artifact": "execution_receipt",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["execution_receipt"],
    },
    EvidenceEventType.VALIDATION_STARTED.value: {
        "future_artifact": "evidence_contract",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["evidence_contract"],
    },
    EvidenceEventType.VALIDATION_COMPLETED.value: {
        "future_artifact": "evidence_contract",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["evidence_contract"],
    },
    EvidenceEventType.RECEIPT_GENERATED.value: {
        "future_artifact": "execution_receipt",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["execution_receipt"],
    },
    EvidenceEventType.OPERATION_COMPLETED.value: {
        "future_artifact": "execution_receipt",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["execution_receipt"],
    },
    EvidenceEventType.OPERATION_FAILED.value: {
        "future_artifact": "execution_receipt",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["execution_receipt"],
    },
    EvidenceEventType.OPERATION_ESCALATED.value: {
        "future_artifact": "evidence_contract",
        "sclite_schema_ref": SCLITE_SCHEMA_REFS["evidence_contract"],
    },
}


@dataclass(frozen=True)
class SCLiteArtifactDescriptor:
    artifact_role: str
    sclite_schema_ref: str
    descriptor_path: str | None = None
    digest: str | None = None
    status: str = "placeholder"

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_role": self.artifact_role,
            "sclite_schema_ref": self.sclite_schema_ref,
            "descriptor_path": self.descriptor_path,
            "digest": self.digest,
            "status": self.status,
        }


@dataclass
class SCLiteReceiptExport:
    operation_id: str
    authority: str = RECEIPT_EXPORT_AUTHORITY
    emitter: str = "placeholder"
    migration_note: str = PLACEHOLDER_EMITTER_NOTICE
    artifact_slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    evidence_event_mappings: list[dict[str, str]] = field(default_factory=list)
    internal_evidence_event_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "authority": self.authority,
            "emitter": self.emitter,
            "migration_note": self.migration_note,
            "artifact_slots": dict(self.artifact_slots),
            "evidence_event_mappings": list(self.evidence_event_mappings),
            "internal_evidence_event_ids": list(self.internal_evidence_event_ids),
        }


class SCLiteEmitter(Protocol):
    def export_operation_receipt(
        self,
        *,
        operation_id: str,
        events: list[dict[str, Any]],
        plan_summary: dict[str, Any] | None = None,
    ) -> SCLiteReceiptExport: ...
