from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rexecop.evidence.public_projection import (
    AUDIENCE_RUNTIME_DIAGNOSTIC,
    sanitize_for_audience,
)
from rexecop.evidence.redaction import redact_text
from rexecop.observability.failure_classes import is_valid_failure_class

if TYPE_CHECKING:
    from rexecop.storage.port import RuntimeStore

STRUCTURED_LOG_EVENT_SCHEMA = "rexecop.structured_log_event.v0.1"
STRUCTURED_LOG_LIST_SCHEMA = "rexecop.structured_log_list.v0.1"

REF_KINDS = frozenset(
    {
        "operation_id",
        "plan_id",
        "admission_ref",
        "spec_ref",
        "receipt_ref",
        "evidence_ref",
    }
)


@dataclass(frozen=True)
class StructuredLogRefs:
    operation_id: str = ""
    plan_id: str = ""
    admission_ref: str = ""
    spec_ref: str = ""
    receipt_ref: str = ""
    evidence_ref: str = ""

    def as_dict(self) -> dict[str, str]:
        payload = {
            "operation_id": self.operation_id,
            "plan_id": self.plan_id,
            "admission_ref": self.admission_ref,
            "spec_ref": self.spec_ref,
            "receipt_ref": self.receipt_ref,
            "evidence_ref": self.evidence_ref,
        }
        return {key: value for key, value in payload.items() if value}


def build_structured_log_event(
    *,
    event_kind: str,
    correlation_id: str,
    message: str,
    refs: StructuredLogRefs | None = None,
    failure_class: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_failure = str(failure_class or "").strip()
    if normalized_failure and not is_valid_failure_class(normalized_failure):
        raise ValueError(f"unsupported failure_class: {normalized_failure}")
    event = {
        "schema": STRUCTURED_LOG_EVENT_SCHEMA,
        "event_id": f"obs-{uuid.uuid4().hex[:12]}",
        "timestamp_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "event_kind": str(event_kind or "").strip(),
        "correlation_id": str(correlation_id or "").strip(),
        "message": redact_text(message),
        "audience": AUDIENCE_RUNTIME_DIAGNOSTIC,
        "refs": (refs or StructuredLogRefs()).as_dict(),
        "details": sanitize_for_audience(
            details or {}, audience=AUDIENCE_RUNTIME_DIAGNOSTIC
        ),
        "non_claims": [
            "Structured logs are bounded runtime projections only.",
            "Does not expose raw secrets or private connector payloads.",
            "Does not execute remediation or mutate truth stores.",
        ],
    }
    if normalized_failure:
        event["failure_class"] = normalized_failure
    return event


def list_structured_logs(
    store: RuntimeStore,
    *,
    operation_id: str = "",
    correlation_id: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit), 200))
    items = store.list_structured_log_events(
        operation_id=operation_id or None,
        correlation_id=correlation_id or None,
        limit=bounded_limit,
    )
    return {
        "schema": STRUCTURED_LOG_LIST_SCHEMA,
        "runtime_root": str(store.root),
        "count": len(items),
        "items": items,
        "filters": {
            "operation_id": operation_id or None,
            "correlation_id": correlation_id or None,
            "limit": bounded_limit,
        },
        "non_claims": [
            "Listing is bounded and redacted.",
            "Does not replace evidence events or SCLite truth artifacts.",
        ],
    }
