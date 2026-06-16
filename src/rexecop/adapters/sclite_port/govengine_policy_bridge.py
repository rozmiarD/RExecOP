from __future__ import annotations

from typing import Any


def govengine_admission_metadata(operation: Any) -> dict[str, Any]:
    raw = operation.metadata.get("govengine_admission")
    return dict(raw) if isinstance(raw, dict) else {}


def policy_reason_codes(operation: Any, plan: Any) -> list[str]:
    admission = govengine_admission_metadata(operation)
    details = admission.get("details")
    if isinstance(details, dict):
        blockers = details.get("blockers")
        if isinstance(blockers, list) and blockers:
            return [str(item) for item in blockers]
        if details.get("status"):
            return [str(details["status"])]
    if operation.govengine_decision_type:
        return [operation.govengine_decision_type]
    if plan.mode in {"dry_run", "observe"}:
        return ["dry_run_default"]
    return ["govengine_not_evaluated"]


def policy_summary(operation: Any) -> str:
    admission = govengine_admission_metadata(operation)
    if admission.get("summary"):
        return str(admission["summary"])
    return operation.govengine_decision_summary or "rexecop governance summary"
