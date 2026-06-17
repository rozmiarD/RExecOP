from __future__ import annotations

from typing import Any

from rexecop.operation.model import Operation
from rexecop.storage.port import RuntimeStore


def build_escalation_package(
    *,
    operation: Operation,
    store: RuntimeStore,
    failed_step_id: str = "",
    safe_next_options: list[str] | None = None,
) -> dict[str, Any]:
    evidence = store.list_evidence_events(operation.id)
    return {
        "operation_id": operation.id,
        "state": operation.state,
        "failed_step_id": failed_step_id or operation.current_step_id,
        "govengine_decision_type": operation.govengine_decision_type,
        "govengine_decision_summary": operation.govengine_decision_summary,
        "evidence_event_ids": [event["event_id"] for event in evidence],
        "safe_next_options": safe_next_options
        or [
            "review_evidence_history",
            "export_receipt",
            "cancel_operation",
        ],
        "sclite_refs": dict(operation.sclite_refs),
    }
