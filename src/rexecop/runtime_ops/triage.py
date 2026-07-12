from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from govengine import explain_supervisor_action

from rexecop.errors import RExecOpValidationError
from rexecop.evidence.public_projection import (
    AUDIENCE_LOCAL_OPERATOR,
    sanitize_for_audience,
)
from rexecop.operation.model import Operation
from rexecop.operation.state import OperationState
from rexecop.profile.loader import load_profile
from rexecop.profile.operator_metadata import resolve_failure_operator_hints
from rexecop.runtime_ops.coordinator import ACTIVE_RUNTIME_STATES, RuntimeCoordinator
from rexecop.runtime_ops.target_lock import TargetLockManager
from rexecop.runtime_ops.watchdog import supervisor_request_from_record
from rexecop.storage.port import RuntimeStore

RUNTIME_STATUS_SCHEMA = "rexecop.runtime_status.v0.1"
DEAD_LETTER_LIST_SCHEMA = "rexecop.dead_letter_list.v0.1"
DEAD_LETTER_SHOW_SCHEMA = "rexecop.dead_letter_show.v0.1"
LOCKS_LIST_SCHEMA = "rexecop.locks_list.v0.1"
OPS_SCHEMA = "rexecop.ops.v0.1"
EXPLAIN_ERROR_SCHEMA = "rexecop.explain_error.v0.1"

ACTION_REQUIRED_STATES = frozenset(
    {
        OperationState.BLOCKED.value,
        OperationState.FAILED.value,
        OperationState.WAITING_FOR_APPROVAL.value,
        OperationState.ESCALATED.value,
    }
)


def collect_runtime_status(store: RuntimeStore) -> dict[str, Any]:
    coordinator = RuntimeCoordinator(store)
    operations = store.list_operations()
    active = [
        _operation_summary(item)
        for item in operations
        if item.state in ACTIVE_RUNTIME_STATES
    ]
    pending = coordinator.queue.list_pending()
    locks = list_locks(store)
    dead_letters = list_dead_letter_items(store)
    heartbeat = _load_watchdog_heartbeat(store)
    return {
        "schema": RUNTIME_STATUS_SCHEMA,
        "runtime_root": str(store.root),
        "queue": {
            "pending": pending,
            "depth": len(pending),
        },
        "operations": {
            "total": len(operations),
            "active": active,
            "active_count": len(active),
        },
        "locks": {
            "count": len(locks),
            "stale_count": sum(1 for item in locks if item.get("stale")),
        },
        "dead_letter": {"count": len(dead_letters)},
        "inbox": {"count": _inbox_count(store)},
        "watchdog": {"heartbeat": heartbeat},
    }


def collect_ops_snapshot(store: RuntimeStore) -> dict[str, Any]:
    coordinator = RuntimeCoordinator(store)
    operations = store.list_operations()
    pending = coordinator.queue.list_pending()
    locks = list_locks(store)
    dead_letters = list_dead_letter_items(store)
    action_required = _collect_action_required(store, operations, locks, dead_letters)
    blockers = [
        item
        for item in action_required
        if item.get("severity") == "blocker"
    ]
    return {
        "schema": OPS_SCHEMA,
        "runtime_root": str(store.root),
        "queue": {
            "pending": pending,
            "depth": len(pending),
        },
        "active_operations": [
            _operation_summary(item)
            for item in operations
            if item.state in ACTIVE_RUNTIME_STATES
        ],
        "action_required": action_required,
        "blockers": blockers,
        "dead_letters": {
            "count": len(dead_letters),
            "items": dead_letters,
        },
        "locks": {
            "count": len(locks),
            "stale_count": sum(1 for item in locks if item.get("stale")),
            "items": locks,
        },
        "safe_next_actions": _ops_safe_next_actions(action_required, blockers),
        "non_claims": [
            "Aggregates runtime telemetry only; SCLite remains truth authority.",
            "Does not execute recovery actions automatically.",
        ],
    }


def list_dead_letter_items(store: RuntimeStore) -> list[dict[str, Any]]:
    directory = store.root / "dead_letter"
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC)
                .replace(microsecond=0)
                .isoformat(),
            }
        )
    return items


def show_dead_letter_item(store: RuntimeStore, name: str) -> dict[str, Any]:
    path = _dead_letter_path(store, name)
    raw = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RExecOpValidationError(f"dead-letter payload is not JSON: {name}") from exc
    if not isinstance(payload, dict):
        raise RExecOpValidationError(f"dead-letter payload must be an object: {name}")
    return {
        "schema": DEAD_LETTER_SHOW_SCHEMA,
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "modified_at": datetime.fromtimestamp(path.stat().st_mtime, UTC)
        .replace(microsecond=0)
        .isoformat(),
        "audience": AUDIENCE_LOCAL_OPERATOR,
        "payload": sanitize_for_audience(payload, audience=AUDIENCE_LOCAL_OPERATOR),
        "non_claims": [
            "Redacted operator view only; dead-letter file remains runtime-local.",
            "Does not replay or reprocess inbox triggers automatically.",
        ],
    }


def list_dead_letter_manifest(store: RuntimeStore) -> dict[str, Any]:
    return {
        "schema": DEAD_LETTER_LIST_SCHEMA,
        "runtime_root": str(store.root),
        "items": list_dead_letter_items(store),
    }


def list_locks(store: RuntimeStore) -> list[dict[str, Any]]:
    manager = TargetLockManager(store)
    directory = manager.locks_dir
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.lock")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            record = {}
        if not isinstance(record, dict):
            record = {}
        stale = manager.is_stale(record) if record else True
        items.append(
            {
                "name": path.name,
                "environment": str(record.get("environment") or ""),
                "target": str(record.get("target") or ""),
                "operation_id": str(record.get("operation_id") or ""),
                "acquired_at": str(record.get("acquired_at") or ""),
                "stale": stale,
            }
        )
    return items


def list_locks_manifest(store: RuntimeStore) -> dict[str, Any]:
    locks = list_locks(store)
    return {
        "schema": LOCKS_LIST_SCHEMA,
        "runtime_root": str(store.root),
        "items": locks,
        "stale_count": sum(1 for item in locks if item.get("stale")),
    }


def explain_error(store: RuntimeStore, ref: str) -> dict[str, Any]:
    normalized = str(ref or "").strip()
    if not normalized:
        raise RExecOpValidationError("explain-error ref must not be empty")
    if normalized.startswith("op-"):
        return _explain_operation_error(store, normalized)
    if normalized.endswith(".json") and (store.root / "dead_letter" / normalized).is_file():
        return _explain_dead_letter_error(store, normalized)
    if normalized.startswith("wd-"):
        return _explain_watchdog_record_error(store, normalized)
    if normalized.endswith(".lock"):
        return _explain_lock_error(store, normalized)
    if _looks_like_operation_id(normalized):
        return _explain_operation_error(store, normalized)
    raise RExecOpValidationError(f"unsupported explain-error ref: {normalized}")


def _explain_operation_error(store: RuntimeStore, operation_id: str) -> dict[str, Any]:
    operation = store.load_operation(operation_id)
    plan = None
    try:
        plan = store.load_plan(operation_id)
    except RExecOpValidationError:
        plan = None
    failure_class, reason_code, summary = _classify_operation(operation)
    runbook_ref = ""
    operator_hints: dict[str, Any] = {}
    if plan is not None:
        from rexecop.operation.review import review_operation

        review = review_operation(operation, plan)
        runbook_ref = str(review["decision_screen"].get("runbook_ref") or "")
        operator_hints = dict(review["decision_screen"].get("operator_hints") or {})
    safe_next_actions = _operation_safe_next_actions(operation, failure_class)
    profile_hints = _profile_failure_hints(operation, failure_class)
    if profile_hints.get("operator_summary"):
        summary = str(profile_hints["operator_summary"])
    if profile_hints.get("runbook_hint") and not operator_hints.get("runbook_hint"):
        operator_hints["runbook_hint"] = profile_hints["runbook_hint"]
    hint_actions = list(profile_hints.get("safe_next_options") or [])
    if hint_actions:
        safe_next_actions = _merge_safe_next_actions(hint_actions, safe_next_actions)
    payload = {
        "schema": EXPLAIN_ERROR_SCHEMA,
        "ref": operation_id,
        "ref_kind": "operation",
        "failure_class": failure_class,
        "reason_code": reason_code,
        "summary": summary,
        "operation": _operation_summary(operation),
        "runbook_ref": runbook_ref,
        "safe_next_actions": safe_next_actions,
        "non_claims": [
            "Classification is operator guidance only.",
            "Does not execute recovery or mutate runtime state.",
        ],
    }
    if operator_hints:
        payload["operator_hints"] = operator_hints
    return payload


def _explain_dead_letter_error(store: RuntimeStore, name: str) -> dict[str, Any]:
    item = show_dead_letter_item(store, name)
    payload = item["payload"]
    return {
        "schema": EXPLAIN_ERROR_SCHEMA,
        "ref": name,
        "ref_kind": "dead_letter",
        "failure_class": "runtime",
        "reason_code": "dead_letter_item",
        "summary": "Inbox trigger payload was moved to dead-letter and requires operator review.",
        "dead_letter": {
            "name": name,
            "payload_keys": sorted(str(key) for key in payload.keys())
            if isinstance(payload, dict)
            else [],
        },
        "safe_next_actions": [
            f"rexecop dead-letter show {name}",
            "Review the redacted payload and recreate the trigger with corrected inputs.",
            "rexecop ops",
        ],
        "non_claims": item["non_claims"],
    }


def _explain_watchdog_record_error(store: RuntimeStore, record_id: str) -> dict[str, Any]:
    record = _load_watchdog_record(store, record_id)
    observation = str(record.get("observation") or "")
    decision = str(record.get("decision") or "")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    reason = str(payload.get("reason") or decision or observation)
    operation_id = str(payload.get("operation_id") or "")
    gov_explanation = explain_supervisor_action(
        supervisor_request_from_record(record)
    ).as_dict()
    failure_class = "runtime"
    safe_next = list(gov_explanation.get("safe_next_actions") or ["rexecop ops"])
    return {
        "schema": EXPLAIN_ERROR_SCHEMA,
        "ref": record_id,
        "ref_kind": "watchdog_record",
        "failure_class": failure_class,
        "reason_code": str(gov_explanation.get("reason_code") or reason),
        "summary": str(
            gov_explanation.get("operator_summary")
            or f"Watchdog recorded {observation} -> {decision}."
        ),
        "watchdog": {
            "record_id": record_id,
            "observation": observation,
            "decision": decision,
            "operation_id": operation_id,
            "recovery_class": gov_explanation.get("recovery_class"),
            "evaluation_path": gov_explanation.get("evaluation_path"),
            "request_digest": gov_explanation.get("request_digest"),
            "admission_digest": gov_explanation.get("admission_digest"),
        },
        "govengine_supervisor_explanation": {
            "schema_version": gov_explanation.get("schema_version"),
            "status": gov_explanation.get("status"),
            "outcome": gov_explanation.get("outcome"),
            "allowed": gov_explanation.get("allowed"),
            "blockers": gov_explanation.get("blockers"),
            "gates_checked": gov_explanation.get("gates_checked"),
        },
        "safe_next_actions": safe_next,
        "non_claims": list(gov_explanation.get("non_claims") or [])
        + [
            "Watchdog records are runtime projections; SCLite owns review semantics.",
        ],
    }


def _explain_lock_error(store: RuntimeStore, name: str) -> dict[str, Any]:
    path = store.root / "locks" / name
    if not path.is_file():
        raise RExecOpValidationError(f"lock not found: {name}")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise RExecOpValidationError(f"invalid lock record: {name}")
    manager = TargetLockManager(store)
    stale = manager.is_stale(record)
    operation_id = str(record.get("operation_id") or "")
    failure_class = "target" if not stale else "runtime"
    return {
        "schema": EXPLAIN_ERROR_SCHEMA,
        "ref": name,
        "ref_kind": "lock",
        "failure_class": failure_class,
        "reason_code": "stale_target_lock" if stale else "target_lock_held",
        "summary": "Target lock is stale and safe to clear."
        if stale
        else "Target lock is held by an active operation.",
        "lock": {
            "name": name,
            "environment": str(record.get("environment") or ""),
            "target": str(record.get("target") or ""),
            "operation_id": operation_id,
            "stale": stale,
        },
        "safe_next_actions": [
            "rexecop locks list",
            *( [f"rexecop explain-error {operation_id}"] if operation_id else [] ),
            "rexecop ops",
        ],
        "non_claims": [
            "Does not release locks automatically.",
        ],
    }


def _classify_operation(
    operation: Operation,
) -> tuple[str, str, str]:
    metadata = operation.metadata
    verdict = metadata.get("policy_verdict")
    if isinstance(verdict, dict):
        decision = str(verdict.get("decision") or "")
        reason_code = str(verdict.get("reason_code") or "")
        if decision == "deny":
            return "policy", reason_code or "policy_denied", operation.govengine_decision_summary
    if operation.state == OperationState.WAITING_FOR_APPROVAL.value:
        return (
            "policy",
            "approval_required",
            "Operation is waiting for manual approval before start.",
        )
    if operation.state == OperationState.BLOCKED.value:
        return (
            "policy",
            operation.govengine_decision_type or "blocked",
            operation.govengine_decision_summary or "Operation is blocked by governance.",
        )
    if operation.state == OperationState.FAILED.value:
        rollback = metadata.get("rollback")
        if isinstance(rollback, dict) and rollback.get("status") == "failed":
            return "connector", "rollback_failed", "Explicit rollback steps failed."
        return "connector", "operation_failed", "Operation execution failed."
    queue = metadata.get("queue")
    if isinstance(queue, dict):
        reason = str(queue.get("reason") or "")
        if reason == "target_locked":
            return "target", reason, "Operation is queued because the target lock is busy."
        if reason == "max_concurrent_reached":
            return "runtime", reason, "Operation is queued because runtime capacity is full."
    if operation.state == OperationState.ESCALATED.value:
        return "runtime", "escalated", "Operation was escalated for operator handling."
    enforcement = metadata.get("policy_enforcement")
    if isinstance(enforcement, dict):
        plan = enforcement.get("plan")
        if isinstance(plan, dict) and str(plan.get("status") or "") == "blocked":
            return (
                "mutation-contract",
                str(plan.get("reason_code") or "mutation_contract_incomplete"),
                "Mutating operation is missing a complete governance contract.",
            )
    return "runtime", operation.state, f"Operation is in state {operation.state}."


def _profile_failure_hints(operation: Operation, failure_class: str) -> dict[str, Any]:
    profile_root_raw = str(operation.metadata.get("profile_root") or "").strip()
    if not profile_root_raw:
        return {}
    profile_root = Path(profile_root_raw)
    if not profile_root.exists():
        return {}
    try:
        profile = load_profile(profile_root)
        return resolve_failure_operator_hints(profile, operation.intent, failure_class)
    except RExecOpValidationError:
        return {}


def _merge_safe_next_actions(
    preferred: list[str],
    base_actions: list[str],
) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for action in [*preferred, *base_actions]:
        text = str(action or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)
    return merged


def _operation_safe_next_actions(operation: Operation, failure_class: str) -> list[str]:
    operation_id = operation.id
    actions = [
        f"rexecop status --operation {operation_id}",
        f"rexecop history --operation {operation_id}",
    ]
    if failure_class == "policy":
        actions.append(f"rexecop operation review --operation {operation_id}")
    if operation.state == OperationState.FAILED.value:
        actions.append(f"rexecop escalate --operation {operation_id}")
    if operation.state in {
        OperationState.PLANNED.value,
        OperationState.APPROVED.value,
    }:
        actions.append(f"rexecop operation diff --operation {operation_id}")
    actions.append("rexecop ops")
    return actions


def _collect_action_required(
    store: RuntimeStore,
    operations: list[Operation],
    locks: list[dict[str, Any]],
    dead_letters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for operation in operations:
        if operation.state in ACTION_REQUIRED_STATES:
            failure_class, reason_code, summary = _classify_operation(operation)
            items.append(
                {
                    "kind": "operation",
                    "severity": "blocker"
                    if operation.state
                    in {OperationState.BLOCKED.value, OperationState.FAILED.value}
                    else "action_required",
                    "operation_id": operation.id,
                    "state": operation.state,
                    "failure_class": failure_class,
                    "reason_code": reason_code,
                    "summary": summary,
                }
            )
        queue = operation.metadata.get("queue")
        if isinstance(queue, dict) and operation.state == OperationState.APPROVED.value:
            items.append(
                {
                    "kind": "queued_operation",
                    "severity": "action_required",
                    "operation_id": operation.id,
                    "state": operation.state,
                    "failure_class": "runtime",
                    "reason_code": str(queue.get("reason") or "queued"),
                    "summary": "Approved operation is waiting in the run-now queue.",
                }
            )
    for lock in locks:
        if lock.get("stale"):
            items.append(
                {
                    "kind": "stale_lock",
                    "severity": "action_required",
                    "lock_name": lock.get("name"),
                    "operation_id": lock.get("operation_id"),
                    "failure_class": "runtime",
                    "reason_code": "stale_target_lock",
                    "summary": "Stale target lock should be reviewed before new starts.",
                }
            )
    for item in dead_letters:
        items.append(
            {
                "kind": "dead_letter",
                "severity": "action_required",
                "name": item.get("name"),
                "failure_class": "runtime",
                "reason_code": "dead_letter_item",
                "summary": "Dead-letter item requires operator triage.",
            }
        )
    inbox_count = _inbox_count(store)
    if inbox_count:
        items.append(
            {
                "kind": "inbox_backlog",
                "severity": "action_required",
                "count": inbox_count,
                "failure_class": "runtime",
                "reason_code": "inbox_backlog",
                "summary": "Trigger inbox has pending JSON items.",
            }
        )
    return items


def _ops_safe_next_actions(
    action_required: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
) -> list[str]:
    actions = ["rexecop runtime status --json", "rexecop locks list", "rexecop dead-letter list"]
    if blockers:
        first = blockers[0]
        if first.get("operation_id"):
            actions.append(f"rexecop explain-error {first['operation_id']}")
        elif first.get("name"):
            actions.append(f"rexecop explain-error {first['name']}")
    elif action_required:
        first = action_required[0]
        if first.get("operation_id"):
            actions.append(f"rexecop explain-error {first['operation_id']}")
        elif first.get("name"):
            actions.append(f"rexecop dead-letter show {first['name']}")
    return actions


def _operation_summary(operation: Operation) -> dict[str, str]:
    return {
        "operation_id": operation.id,
        "state": operation.state,
        "profile": operation.profile,
        "intent": operation.intent,
        "target": operation.target,
        "mode": operation.mode,
        "updated_at": operation.updated_at,
    }


def _dead_letter_path(store: RuntimeStore, name: str) -> Path:
    if "/" in name or ".." in name:
        raise RExecOpValidationError(f"invalid dead-letter name: {name}")
    path = store.root / "dead_letter" / name
    if not path.is_file():
        raise RExecOpValidationError(f"dead-letter item not found: {name}")
    return path


def _inbox_count(store: RuntimeStore) -> int:
    inbox = store.root / "inbox"
    if not inbox.is_dir():
        return 0
    return len(list(inbox.glob("*.json")))


def _load_watchdog_heartbeat(store: RuntimeStore) -> dict[str, Any] | None:
    path = store.root / "watchdog" / "heartbeat.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _load_watchdog_record(store: RuntimeStore, record_id: str) -> dict[str, Any]:
    records_dir = store.root / "watchdog" / "records"
    if not records_dir.is_dir():
        raise RExecOpValidationError(f"watchdog record not found: {record_id}")
    for path in sorted(records_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and str(payload.get("record_id") or "") == record_id:
            return payload
    raise RExecOpValidationError(f"watchdog record not found: {record_id}")


def _looks_like_operation_id(ref: str) -> bool:
    return ref.startswith("op-")
