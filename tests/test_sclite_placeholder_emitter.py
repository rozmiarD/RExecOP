from __future__ import annotations

from rexecop.adapters.sclite_port.contracts import (
    ARTIFACT_SLOTS,
    EVENT_SCLITE_MAPPING,
    PLACEHOLDER_EMITTER_NOTICE,
    RECEIPT_EXPORT_AUTHORITY,
    SCLITE_SCHEMA_REFS,
)
from rexecop.adapters.sclite_port.placeholder_emitter import PlaceholderSCLiteEmitter
from rexecop.evidence.event import EvidenceEventType


def test_all_evidence_event_types_have_sclite_mapping() -> None:
    for event_type in EvidenceEventType:
        assert event_type.value in EVENT_SCLITE_MAPPING


def test_artifact_slots_include_schema_refs() -> None:
    for role in ARTIFACT_SLOTS:
        assert role in SCLITE_SCHEMA_REFS
        assert SCLITE_SCHEMA_REFS[role].startswith("schemas/")


def test_placeholder_emitter_marks_non_authoritative() -> None:
    emitter = PlaceholderSCLiteEmitter()
    export = emitter.export_operation_receipt(
        operation_id="op-test",
        events=[
            {
                "event_id": "ev-1",
                "event_type": EvidenceEventType.OPERATION_CREATED.value,
                "sanitized_payload": {},
            }
        ],
    )
    assert export.authority == RECEIPT_EXPORT_AUTHORITY
    assert export.emitter == "placeholder"
    assert "bootstrap/offline only" in export.migration_note
    assert PLACEHOLDER_EMITTER_NOTICE in export.migration_note
    for role in ARTIFACT_SLOTS:
        slot = export.artifact_slots[role]
        assert slot["sclite_schema_ref"] == SCLITE_SCHEMA_REFS[role]
        assert slot["status"] == "placeholder"


def test_placeholder_emitter_maps_events() -> None:
    emitter = PlaceholderSCLiteEmitter()
    export = emitter.export_operation_receipt(
        operation_id="op-test",
        events=[
            {
                "event_id": "ev-plan",
                "event_type": EvidenceEventType.PLAN_GENERATED.value,
                "sanitized_payload": {},
            }
        ],
    )
    assert export.evidence_event_mappings == [
        {
            "event_id": "ev-plan",
            "event_type": "plan_generated",
            "future_artifact": "execution_contract",
            "sclite_schema_ref": SCLITE_SCHEMA_REFS["execution_contract"],
        }
    ]
