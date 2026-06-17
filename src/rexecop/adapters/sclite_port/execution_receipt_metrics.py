from __future__ import annotations

from collections.abc import Callable
from typing import Any


def connector_step_ids(plan: Any) -> set[str]:
    return {
        str(step["id"])
        for step in plan.planned_steps
        if str(step.get("type") or "") == "connector" and step.get("id")
    }


def planned_connector_command_count(plan: Any) -> int:
    return max(len(connector_step_ids(plan)), 1)


def _completed_connector_steps(
    plan: Any,
    operation: Any,
    evidence_events: list[dict[str, Any]] | None,
) -> set[str]:
    allowed = connector_step_ids(plan)
    if not allowed:
        return set()

    completed: set[str] = set()
    shared_state = dict(operation.metadata.get("shared_state") or {})
    connector_results = shared_state.get("connector_results")
    if isinstance(connector_results, dict):
        completed.update(step_id for step_id in connector_results if step_id in allowed)

    for event in evidence_events or []:
        if str(event.get("event_type") or "") != "step_completed":
            continue
        step_id = str(event.get("step_id") or "")
        payload = event.get("sanitized_payload")
        if not isinstance(payload, dict):
            payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        if not step_id:
            step_id = str(payload.get("step_id") or "")
        if step_id not in allowed:
            continue
        if payload.get("success") is False:
            continue
        completed.add(step_id)

    return completed


def derive_execution_receipt_metrics(
    operation: Any,
    plan: Any,
    *,
    evidence_events: list[dict[str, Any]] | None = None,
    rexecop_mode: Callable[[str], str],
) -> tuple[int, bool]:
    """Return (executed_command_count, network_execution_performed) for scoped receipts."""
    completed = _completed_connector_steps(plan, operation, evidence_events)
    executed_command_count = len(completed)
    capability_mode = rexecop_mode(str(getattr(operation, "mode", plan.mode)))
    network_execution_performed = capability_mode != "dry_run" and executed_command_count > 0
    return executed_command_count, network_execution_performed


def receipt_non_claims(capability_mode: str, *, network_execution_performed: bool) -> list[str]:
    claims = [
        "receipt_does_not_include_raw_logs",
        "receipt_does_not_prove_runtime_enforcement",
    ]
    if capability_mode == "dry_run" or not network_execution_performed:
        claims.insert(1, "receipt_does_not_claim_live_target_execution")
    return claims
