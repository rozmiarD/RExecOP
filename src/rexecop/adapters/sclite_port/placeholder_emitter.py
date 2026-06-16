from __future__ import annotations

from typing import Any

from rexecop.adapters.sclite_port.contracts import (
    ARTIFACT_SLOTS,
    EVENT_SCLITE_MAPPING,
    PLACEHOLDER_EMITTER_NOTICE,
    RECEIPT_EXPORT_AUTHORITY,
    SCLITE_SCHEMA_REFS,
    SCLiteArtifactDescriptor,
    SCLiteReceiptExport,
)


class PlaceholderSCLiteEmitter:
    """Bootstrap/offline receipt export with SCLite schema references only."""

    emitter_name = "placeholder"

    def export_operation_receipt(
        self,
        *,
        operation_id: str,
        events: list[dict[str, Any]],
        plan_summary: dict[str, Any] | None = None,
    ) -> SCLiteReceiptExport:
        artifact_slots = {
            role: SCLiteArtifactDescriptor(
                artifact_role=role,
                sclite_schema_ref=SCLITE_SCHEMA_REFS[role],
                descriptor_path=None,
                digest=None,
                status="placeholder",
            ).as_dict()
            for role in ARTIFACT_SLOTS
        }

        mappings: list[dict[str, str]] = []
        event_ids: list[str] = []
        for event in events:
            event_type = str(event.get("event_type") or "")
            event_ids.append(str(event.get("event_id") or ""))
            mapping = EVENT_SCLITE_MAPPING.get(event_type)
            if mapping is None:
                continue
            mappings.append(
                {
                    "event_id": str(event.get("event_id") or ""),
                    "event_type": event_type,
                    "future_artifact": mapping["future_artifact"],
                    "sclite_schema_ref": mapping["sclite_schema_ref"],
                }
            )

        export = SCLiteReceiptExport(
            operation_id=operation_id,
            authority=RECEIPT_EXPORT_AUTHORITY,
            emitter=self.emitter_name,
            migration_note=PLACEHOLDER_EMITTER_NOTICE,
            artifact_slots=artifact_slots,
            evidence_event_mappings=mappings,
            internal_evidence_event_ids=[item for item in event_ids if item],
        )
        if plan_summary:
            export.artifact_slots["intent_contract"]["summary"] = {
                "profile": plan_summary.get("profile"),
                "intent": plan_summary.get("intent"),
                "target": plan_summary.get("target"),
                "mode": plan_summary.get("mode"),
            }
        return export

    def build_sclite_refs(self, export: SCLiteReceiptExport) -> dict[str, Any]:
        refs: dict[str, Any] = {}
        for role, slot in export.artifact_slots.items():
            refs[role] = {
                "sclite_schema_ref": slot["sclite_schema_ref"],
                "descriptor_path": slot.get("descriptor_path"),
                "digest": slot.get("digest"),
                "status": slot.get("status", "placeholder"),
            }
        return refs
