from __future__ import annotations

from typing import Any

from rexecop.catalog.model import TargetDescriptor
from rexecop.errors import RExecOpValidationError

OPERATIONS_UNAVAILABLE_SCHEMA = "rexecop.operations_unavailable.v0.1"

WHY_UNAVAILABLE = {
    "unsupported_profile": "Catalog profile_ref does not match the operation profile.",
    "unsupported_target_kind": "Target kind is not declared for this operation.",
    "missing_capability": "Target capabilities do not satisfy the operation contract.",
    "missing_connector": "Target connector_refs do not include a required workflow connector.",
}


def build_unavailable_operations_report(
    service: Any,
    target_id: str,
    *,
    intent: str | None = None,
) -> dict[str, Any]:
    target = service.get_target(target_id)
    items = service.list_operations_for_target(target_id)
    if intent is not None and not any(item["operation"]["id"] == intent for item in items):
        raise RExecOpValidationError(f"unknown profile intent: {intent}")

    unavailable: list[dict[str, Any]] = []
    available_count = 0
    for item in items:
        operation = item["operation"]
        if intent is not None and operation["id"] != intent:
            continue
        applicability = item["applicability"]
        if applicability["applicable"]:
            available_count += 1
            continue
        unavailable.append(
            _unavailable_entry(
                target=target,
                operation=operation,
                applicability=applicability,
            )
        )

    return {
        "schema": OPERATIONS_UNAVAILABLE_SCHEMA,
        "target": target.public_dict(),
        "summary": {
            "available_count": available_count,
            "unavailable_count": len(unavailable),
            "technical_admission_note": (
                "Applicability is technical only; GovEngine admission is still required "
                "for operations marked admission_required."
            ),
        },
        "unavailable": unavailable,
    }


def _unavailable_entry(
    *,
    target: TargetDescriptor,
    operation: dict[str, Any],
    applicability: dict[str, Any],
) -> dict[str, Any]:
    return {
        "operation_id": operation["id"],
        "title": operation["title"],
        "profile_ref": operation["profile_ref"],
        "status": applicability["status"],
        "reason_codes": list(applicability["reason_codes"]),
        "missing_capabilities": list(applicability.get("missing_capabilities") or []),
        "missing_connectors": list(applicability.get("missing_connectors") or []),
        "why_unavailable": _why_unavailable(applicability),
        "safe_next_options": _safe_next_options(
            target_id=target.id,
            operation=operation,
            applicability=applicability,
        ),
    }


def _why_unavailable(applicability: dict[str, Any]) -> str:
    status = str(applicability.get("status") or "")
    base = WHY_UNAVAILABLE.get(
        status,
        "Operation is not technically applicable to this target.",
    )
    missing_capabilities = applicability.get("missing_capabilities") or []
    missing_connectors = applicability.get("missing_connectors") or []
    if missing_capabilities:
        return f"{base} Missing capabilities: {', '.join(missing_capabilities)}."
    if missing_connectors:
        return f"{base} Missing connectors: {', '.join(missing_connectors)}."
    return base


def _safe_next_options(
    *,
    target_id: str,
    operation: dict[str, Any],
    applicability: dict[str, Any],
) -> list[str]:
    status = str(applicability.get("status") or "")
    options: list[str] = []
    if status == "unsupported_profile":
        options.append(
            "Align catalog profile_ref with the operation profile or choose a matching target."
        )
    elif status == "unsupported_target_kind":
        options.append(
            "Update the catalog target kind or select a target kind declared by the operation: "
            + ", ".join(operation.get("target_kinds") or [])
        )
    elif status == "missing_capability":
        missing = applicability.get("missing_capabilities") or []
        options.append(
            "Add required capabilities to the catalog target entry: " + ", ".join(missing)
        )
    elif status == "missing_connector":
        missing = applicability.get("missing_connectors") or []
        options.append(
            "Add required connector_refs to the catalog target entry: " + ", ".join(missing)
        )
    options.append(f"rexecop targets show {target_id} --catalog <catalog>")
    options.append(
        f"rexecop operations explain {operation['id']} --profile {operation['profile_ref']}"
    )
    runbook_ref = str(operation.get("runbook_ref") or "").strip()
    if runbook_ref:
        options.append(
            f"rexecop runbook show {operation['id']} --profile {operation['profile_ref']}"
        )
    return options