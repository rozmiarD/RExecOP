from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from rexecop.adapters.sclite_port.placeholder_emitter import PlaceholderSCLiteEmitter
from rexecop.evidence.event import EvidenceEventType

if TYPE_CHECKING:
    from rexecop.operation.controller import OperationController

DEPRECATION_MESSAGE = (
    "export_placeholder_receipt is deprecated bootstrap/lab API only. "
    "Use export_receipt (SCLiteArtifactEmitter) for authoritative bundles."
)


def export_placeholder_receipt(
    controller: OperationController,
    operation_id: str,
) -> dict[str, object]:
    """Deprecated non-authoritative receipt export for offline lab/bootstrap."""
    operation = controller.get_operation(operation_id)
    plan = controller.store.load_plan(operation_id)
    events = controller.store.list_evidence_events(operation_id)
    emitter = PlaceholderSCLiteEmitter()
    export = emitter.export_operation_receipt(
        operation_id=operation_id,
        events=events,
        plan_summary={
            "profile": plan.profile,
            "intent": plan.intent,
            "target": plan.target,
            "mode": plan.mode,
        },
    )
    operation.sclite_refs = emitter.build_sclite_refs(export)
    path = controller.store.save_receipt_export(operation_id, export.as_dict())
    receipt_event = controller.evidence.emit(
        operation_id=operation_id,
        event_type=EvidenceEventType.RECEIPT_GENERATED,
        correlation_id=operation.correlation_id,
        state_before=operation.state,
        state_after=operation.state,
        payload={
            "authority": export.authority,
            "emitter": export.emitter,
            "receipt_export_path": str(path),
        },
    )
    operation.evidence_event_ids.append(receipt_event)
    controller.store.save_operation(operation)
    return {"export": export.as_dict(), "path": str(path), "sclite_refs": operation.sclite_refs}


def export_placeholder_receipt_with_warning(
    controller: OperationController,
    operation_id: str,
) -> dict[str, object]:
    warnings.warn(DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
    return export_placeholder_receipt(controller, operation_id)
