from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sclite.artifacts import artifact_sha256

from rexecop.errors import RExecOpValidationError
from rexecop.evidence.public_projection import (
    AUDIENCE_SUPPORT_BUNDLE,
    sanitize_for_audience,
)
from rexecop.evidence.redaction import (
    REDACTED,
    contains_strong_secret_pattern,
    redact_payload,
)
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.storage.port import RuntimeStore
from rexecop.truth_path import project_truth_path

RECEIPT_SHOW_SCHEMA = "rexecop.receipt_show.v0.1"
EVIDENCE_SHOW_SCHEMA = "rexecop.evidence_show.v0.1"
CHAIN_SUMMARY_SCHEMA = "rexecop.chain_summary.v0.1"
CHAIN_EXPLAIN_SCHEMA = "rexecop.chain_explain.v0.1"
SUPPORT_BUNDLE_SCHEMA = "rexecop.support_bundle.v0.1"

_PREVIEW_CHAR_LIMIT = 2048


def show_receipt(
    operation: Operation,
    plan: OperationPlan,
    store: RuntimeStore,
) -> dict[str, Any]:
    """Project redacted receipt/SCLite references without becoming truth authority."""
    del plan
    export, export_status = _load_receipt_export(operation.id, store)
    artifacts = _sclite_artifact_summaries(operation.sclite_refs, store)
    missing = [
        item["role"]
        for item in artifacts
        if item["path_status"] == "missing" or item["integrity_status"] == "missing"
    ]
    broken = [
        item["role"]
        for item in artifacts
        if item["integrity_status"] == "broken_digest"
    ]
    return {
        "schema": RECEIPT_SHOW_SCHEMA,
        "status": _receipt_status(export_status, missing, broken),
        "operation_id": operation.id,
        "operation": _operation_summary(operation),
        "receipt_export": _receipt_export_summary(operation.id, export, export_status, store),
        "sclite_refs": {
            "status": "present" if artifacts else "absent",
            "artifacts": artifacts,
        },
        "missing_artifacts": missing,
        "broken_artifacts": broken,
        "non_claims": [
            "Does not canonicalize or replace SCLite artifacts.",
            "Does not treat RExecOp receipt exports as truth.",
            "Does not print raw connector output or secret values.",
        ],
    }


def show_evidence(
    operation: Operation,
    store: RuntimeStore,
) -> dict[str, Any]:
    events = [redact_payload(event) for event in store.list_evidence_events(operation.id)]
    event_types = Counter(str(event.get("event_type") or "") for event in events)
    previews = [_event_preview(event) for event in events]
    sensitivity = _sensitivity_summary(events)
    return {
        "schema": EVIDENCE_SHOW_SCHEMA,
        "status": "present" if events else "missing",
        "operation_id": operation.id,
        "operation": _operation_summary(operation),
        "event_count": len(events),
        "event_types": dict(sorted(event_types.items())),
        "sensitivity": sensitivity,
        "events": previews,
        "non_claims": [
            "Shows RExecOp evidence events, not raw backend logs.",
            "Payloads are redacted and bounded for operator review.",
            "SCLite remains the authority for canonical evidence artifacts.",
        ],
    }


def summarize_chain(
    operation: Operation,
    plan: OperationPlan,
    store: RuntimeStore,
) -> dict[str, Any]:
    events = store.list_evidence_events(operation.id)
    truth_path: dict[str, Any] | None = None
    truth_path_status = "present"
    truth_path_error = ""
    try:
        truth_path = project_truth_path(operation, plan)
    except RExecOpValidationError as exc:
        truth_path_status = "unavailable"
        truth_path_error = str(exc)
    links = _chain_links(operation, events, truth_path)
    return {
        "schema": CHAIN_SUMMARY_SCHEMA,
        "status": "present" if links else "missing",
        "operation_id": operation.id,
        "operation": _operation_summary(operation),
        "truth_path": {
            "status": truth_path_status,
            "schema": truth_path.get("schema", "") if truth_path else "",
            "trace_digest": (
                truth_path.get("governance_trace", {}).get("trace_digest", "")
                if truth_path
                else ""
            ),
            "error": truth_path_error,
        },
        "links": links,
        "replay": {
            "event_count": len(events),
            "operation_history_count": len(operation.history),
            "sclite_ref_count": len(operation.sclite_refs),
        },
        "non_claims": [
            "Summarizes digest-linked runtime/SCLite refs; it is not a ledger.",
            "Does not verify external target state.",
            "Does not execute replay or recovery.",
        ],
    }


def explain_chain(
    operation: Operation,
    plan: OperationPlan,
    store: RuntimeStore,
) -> dict[str, Any]:
    summary = summarize_chain(operation, plan, store)
    reaction = _reaction_explain_summary(operation, store)
    status = "ready"
    if summary["truth_path"]["status"] != "present":
        status = "partial"
    if reaction.get("status") in {"unverified", "unavailable"}:
        status = "partial"
    return {
        "schema": CHAIN_EXPLAIN_SCHEMA,
        "status": status,
        "operation_id": operation.id,
        "operation": summary["operation"],
        "truth_path": summary["truth_path"],
        "reaction": reaction,
        "links": summary["links"],
        "replay": {
            **summary["replay"],
            "reaction_replay_status": str(reaction.get("replay_status") or ""),
        },
        "safe_next_actions": _chain_explain_next_actions(operation.id, reaction),
        "non_claims": [
            "Explains persisted operation/reaction links without executing replay against targets.",
            "Does not replace SCLite reaction-chain or receipt artifacts.",
            "Does not interpret GovEngine policy logic in RExecOp core.",
        ],
    }


def build_support_bundle(
    operation: Operation,
    plan: OperationPlan,
    store: RuntimeStore,
    *,
    redacted: bool,
) -> dict[str, Any]:
    if not redacted:
        raise RExecOpValidationError("support bundle requires --redacted")
    receipt = show_receipt(operation, plan, store)
    evidence = show_evidence(operation, store)
    chain = summarize_chain(operation, plan, store)
    return {
        "schema": SUPPORT_BUNDLE_SCHEMA,
        "status": _support_status(receipt, evidence, chain),
        "operation_id": operation.id,
        "redacted": True,
        "audience": AUDIENCE_SUPPORT_BUNDLE,
        "operation": sanitize_for_audience(
            _operation_summary(operation),
            audience=AUDIENCE_SUPPORT_BUNDLE,
            allowlist={"mode", "state"},
        ),
        "receipt": sanitize_for_audience(
            receipt,
            audience=AUDIENCE_SUPPORT_BUNDLE,
            allowlist={"schema", "status"},
        ),
        "evidence": sanitize_for_audience(
            evidence,
            audience=AUDIENCE_SUPPORT_BUNDLE,
            allowlist={"schema", "status"},
        ),
        "chain": sanitize_for_audience(
            chain,
            audience=AUDIENCE_SUPPORT_BUNDLE,
            allowlist={"schema", "status"},
        ),
        "safe_next_actions": _support_next_actions(operation.id, receipt, evidence, chain),
        "non_claims": [
            "Support bundle is a redacted diagnostic projection only.",
            "It does not include raw secrets, raw backend output, or private connector config.",
            "It does not replace SCLite review bundles or GovEngine admission records.",
        ],
    }


def _operation_summary(operation: Operation) -> dict[str, str]:
    return {
        "profile": operation.profile,
        "environment": operation.environment,
        "intent": operation.intent,
        "target": operation.target,
        "mode": operation.mode,
        "state": operation.state,
        "correlation_id": operation.correlation_id,
    }


def _load_receipt_export(
    operation_id: str,
    store: RuntimeStore,
) -> tuple[dict[str, Any], str]:
    try:
        return redact_payload(store.load_receipt_export(operation_id)), "present"
    except RExecOpValidationError:
        return {}, "missing"


def _receipt_export_summary(
    operation_id: str,
    export: Mapping[str, Any],
    status: str,
    store: RuntimeStore,
) -> dict[str, Any]:
    path = store.root / "receipts" / f"{operation_id}.json"
    summary: dict[str, Any] = {
        "status": status,
        "path": _relative_path(path, store.root),
        "path_status": "present" if path.is_file() else "missing",
    }
    if not export:
        return summary
    summary.update(
        {
            "authority": str(export.get("authority") or ""),
            "emitter": str(export.get("emitter") or ""),
            "bundle_profile": str(export.get("bundle_profile") or ""),
            "bundle_dir": _relative_path(Path(str(export.get("bundle_dir") or "")), store.root),
            "review_verdict": str(export.get("review_verdict") or ""),
            "artifact_roles": [str(item) for item in export.get("artifact_roles") or []],
            "sidecar_files": [str(item) for item in export.get("sidecar_files") or []],
            "export_digest": _payload_digest(export),
        }
    )
    return summary


def _sclite_artifact_summaries(
    refs: Mapping[str, Any],
    store: RuntimeStore,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for role, raw in sorted(refs.items()):
        if not isinstance(raw, Mapping):
            continue
        path = Path(str(raw.get("descriptor_path") or ""))
        resolved = _resolve_descriptor_path(path, store.root)
        expected_digest = _normalize_digest(str(raw.get("digest") or ""))
        actual_digest = ""
        path_status = "missing"
        integrity_status = "unknown"
        digest_match: bool | None = None
        if resolved is not None and resolved.is_file():
            path_status = "present"
            try:
                payload = json.loads(resolved.read_text(encoding="utf-8"))
                actual_digest = _payload_digest(payload)
                if expected_digest:
                    digest_match = actual_digest == expected_digest
                    integrity_status = "ok" if digest_match else "broken_digest"
                else:
                    integrity_status = "not_declared"
            except (OSError, json.JSONDecodeError):
                integrity_status = "unreadable"
        elif str(raw.get("status") or "") == "not_required":
            path_status = "not_required"
            integrity_status = "not_required"
        elif expected_digest:
            integrity_status = "missing"
        artifacts.append(
            {
                "role": str(role),
                "schema_ref": str(raw.get("sclite_schema_ref") or ""),
                "status": str(raw.get("status") or ""),
                "path": _relative_path(path, store.root),
                "path_status": path_status,
                "expected_digest": expected_digest,
                "actual_digest": actual_digest,
                "digest_match": digest_match,
                "integrity_status": integrity_status,
            }
        )
    return artifacts


def _event_preview(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = redact_payload(event.get("sanitized_payload") or {})
    return {
        "event_id": str(event.get("event_id") or ""),
        "event_type": str(event.get("event_type") or ""),
        "timestamp_utc": str(event.get("timestamp_utc") or ""),
        "actor": str(event.get("actor") or ""),
        "state_before": str(event.get("state_before") or ""),
        "state_after": str(event.get("state_after") or ""),
        "step_id": str(event.get("step_id") or ""),
        "correlation_id": str(event.get("correlation_id") or ""),
        "payload_digest": _payload_digest(payload),
        "payload_preview": _bounded_payload(payload),
    }


def _sensitivity_summary(events: list[Any]) -> dict[str, Any]:
    text = json.dumps(events, sort_keys=True)
    return {
        "redaction_marker_count": text.count(REDACTED),
        "strong_secret_pattern_detected": contains_strong_secret_pattern(text),
        "payloads_are_bounded": True,
    }


def _chain_links(
    operation: Operation,
    events: list[dict[str, Any]],
    truth_path: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    links: list[dict[str, str]] = [
        {"kind": "operation", "ref": operation.id, "source": "operation"},
    ]
    if operation.correlation_id:
        links.append(
            {
                "kind": "correlation_id",
                "ref": operation.correlation_id,
                "source": "operation",
            }
        )
    for transition in operation.history:
        links.append(
            {
                "kind": "state_transition",
                "ref": f"{transition.from_state}->{transition.to_state}",
                "source": transition.timestamp_utc,
            }
        )
    for event in events:
        event_id = str(event.get("event_id") or "")
        event_type = str(event.get("event_type") or "")
        if event_id:
            links.append({"kind": f"event:{event_type}", "ref": event_id, "source": "evidence"})
    auto_reaction = operation.metadata.get("auto_reaction")
    if isinstance(auto_reaction, Mapping):
        chain_root = _normalize_digest(str(auto_reaction.get("chain_root") or ""))
        if chain_root:
            links.append({"kind": "reaction_chain", "ref": chain_root, "source": "metadata"})
        child_id = str(auto_reaction.get("child_operation_id") or "")
        if child_id:
            links.append({"kind": "child_operation", "ref": child_id, "source": "metadata"})
    for role, raw in sorted(operation.sclite_refs.items()):
        if not isinstance(raw, Mapping):
            continue
        digest = _normalize_digest(str(raw.get("digest") or ""))
        if digest:
            links.append({"kind": f"sclite_{role}", "ref": digest, "source": "operation"})
    if truth_path is not None:
        for item in truth_path.get("links") or []:
            if not isinstance(item, Mapping):
                continue
            kind = str(item.get("kind") or "")
            ref = str(item.get("ref") or "")
            if kind and ref:
                links.append({"kind": kind, "ref": ref, "source": "truth_path"})
    return _dedupe_links(links)


def _reaction_explain_summary(
    operation: Operation,
    store: RuntimeStore,
) -> dict[str, Any]:
    auto_reaction = operation.metadata.get("auto_reaction")
    if not isinstance(auto_reaction, Mapping):
        return {"status": "absent", "reaction_id": "", "replay_status": ""}
    reaction_id = str(auto_reaction.get("reaction_id") or "")
    if not reaction_id:
        return {"status": "absent", "reaction_id": "", "replay_status": ""}
    try:
        from rexecop.operation.controller import OperationController
        from rexecop.reaction.service import ReactionService

        payload = ReactionService(OperationController(store=store)).explain(reaction_id)
    except RExecOpValidationError as exc:
        return {
            "status": "unavailable",
            "reaction_id": reaction_id,
            "replay_status": "unavailable",
            "error": str(exc),
        }
    child_operation_id = str(payload.get("child_operation_id") or "")
    chain_raw = payload.get("chain")
    chain: Mapping[str, Any] = chain_raw if isinstance(chain_raw, Mapping) else {}
    replay_raw = chain.get("replay")
    replay: Mapping[str, Any] = replay_raw if isinstance(replay_raw, Mapping) else {}
    automation_admission_raw = payload.get("automation_admission")
    automation_admission: Mapping[str, Any] = (
        automation_admission_raw if isinstance(automation_admission_raw, Mapping) else {}
    )
    automation_chain_raw = payload.get("automation_chain")
    automation_chain: Mapping[str, Any] = (
        automation_chain_raw if isinstance(automation_chain_raw, Mapping) else {}
    )
    return {
        "status": str(payload.get("status") or ""),
        "schema": str(payload.get("schema") or ""),
        "reaction_id": reaction_id,
        "outcome": str(payload.get("outcome") or ""),
        "intent_ref": str(payload.get("intent_ref") or ""),
        "child_operation_id": child_operation_id,
        "chain_root": str(chain.get("root_digest") or ""),
        "replay_status": str(replay.get("status") or ""),
        "automation_admission": {
            "status": str(automation_admission.get("status") or ""),
            "decision_id": str(automation_admission.get("decision_id") or ""),
            "decision_digest": str(automation_admission.get("decision_digest") or ""),
            "owner_layer": str(automation_admission.get("owner_layer") or ""),
        },
        "automation_chain": {
            "status": str(automation_chain.get("status") or ""),
            "schema_ref": str(automation_chain.get("schema_ref") or ""),
            "root_digest": str(automation_chain.get("root_digest") or ""),
            "child_edge_count": int(automation_chain.get("child_edge_count") or 0),
        },
    }


def _dedupe_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for item in links:
        key = (item["kind"], item["ref"], item["source"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _support_status(
    receipt: Mapping[str, Any],
    evidence: Mapping[str, Any],
    chain: Mapping[str, Any],
) -> str:
    if receipt.get("status") in {"broken", "missing"}:
        return "action_required"
    if evidence.get("status") == "missing" or chain.get("status") == "missing":
        return "partial"
    return "ready"


def _support_next_actions(
    operation_id: str,
    receipt: Mapping[str, Any],
    evidence: Mapping[str, Any],
    chain: Mapping[str, Any],
) -> list[str]:
    actions = [
        f"rexecop receipt show {operation_id}",
        f"rexecop evidence show {operation_id}",
        f"rexecop chain summary {operation_id}",
    ]
    if receipt.get("status") in {"missing", "partial"}:
        actions.append(f"rexecop export-receipt --operation {operation_id}")
    if receipt.get("status") == "broken":
        actions.append("Inspect SCLite bundle files before trusting this runtime root.")
    if evidence.get("status") == "missing" or chain.get("status") == "missing":
        actions.append(f"rexecop operation explain --operation {operation_id}")
    return actions


def _chain_explain_next_actions(operation_id: str, reaction: Mapping[str, Any]) -> list[str]:
    actions = [
        f"rexecop operation truth-path --operation {operation_id}",
        f"rexecop chain summary {operation_id}",
    ]
    reaction_id = str(reaction.get("reaction_id") or "")
    if reaction_id:
        actions.append(f"rexecop reaction explain --reaction {reaction_id}")
        actions.append(f"rexecop reaction-replay --reaction {reaction_id}")
    child_id = str(reaction.get("child_operation_id") or "")
    if child_id and child_id != operation_id:
        actions.append(f"rexecop operation explain --operation {child_id}")
    return actions


def _receipt_status(export_status: str, missing: list[str], broken: list[str]) -> str:
    if broken:
        return "broken"
    if export_status == "missing":
        return "missing"
    if missing:
        return "partial"
    return "present"


def _resolve_descriptor_path(path: Path, root: Path) -> Path | None:
    if not str(path):
        return None
    if path.is_absolute():
        return path
    candidate = root / path
    if candidate.exists():
        return candidate
    return path


def _relative_path(path: Path, root: Path) -> str:
    if not str(path):
        return ""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except (OSError, ValueError):
        return str(path)


def _payload_digest(payload: Any) -> str:
    normalized = json.loads(json.dumps(payload, sort_keys=True, default=str))
    return _normalize_digest(artifact_sha256(normalized))


def _normalize_digest(value: str) -> str:
    digest = str(value or "").strip()
    if not digest:
        return ""
    return digest if digest.startswith("sha256:") else f"sha256:{digest}"


def _bounded_payload(payload: Any) -> Any:
    text = json.dumps(payload, sort_keys=True, default=str)
    if len(text) <= _PREVIEW_CHAR_LIMIT:
        return payload
    return {
        "truncated": True,
        "char_limit": _PREVIEW_CHAR_LIMIT,
        "payload_digest": _payload_digest(payload),
        "preview": text[:_PREVIEW_CHAR_LIMIT],
    }
