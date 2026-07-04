from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rexecop.catalog.digest import profile_snapshot_digest, yaml_document_digest
from rexecop.catalog.service import CatalogService
from rexecop.errors import RExecOpValidationError
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan

OPERATION_PLAN_DIFF_SCHEMA = "rexecop.operation_plan_diff.v0.1"
CATALOG_BINDING_FIELDS = (
    "catalog_version",
    "catalog_digest",
    "target_descriptor_digest",
    "operation_descriptor_digest",
    "profile_digest",
    "environment_digest",
    "target_id",
    "environment_id",
    "environment_target",
    "profile_ref",
)


def diff_operation_plan(operation: Operation, plan: OperationPlan) -> dict[str, Any]:
    """Compare stored plan bindings against the current profile/env/catalog state."""
    planned_catalog = {
        str(key): str(value) for key, value in dict(plan.catalog_binding).items()
    }
    current_catalog, applicability = _resolve_current_catalog_binding(operation, plan)
    catalog_fields = _compare_binding_fields(planned_catalog, current_catalog)
    profile_field = _compare_digest_field(
        planned_catalog.get("profile_digest", ""),
        _current_profile_digest(operation),
        captured=bool(planned_catalog),
    )
    environment_field = _compare_digest_field(
        planned_catalog.get("environment_digest", ""),
        _current_environment_digest(operation),
        captured=bool(planned_catalog),
    )
    changed_fields = [
        item["field"]
        for item in catalog_fields
        if item.get("changed") is True
    ]
    if profile_field.get("changed") is True:
        changed_fields.append("profile_digest")
    if environment_field.get("changed") is True:
        changed_fields.append("environment_digest")
    if applicability.get("checked") and applicability.get("applicable") is False:
        changed_fields.append("applicability")
    status = _diff_status(
        changed_fields=changed_fields,
        applicability=applicability,
        catalog_captured=bool(planned_catalog),
        catalog_resolvable=current_catalog is not None,
    )
    return {
        "schema": OPERATION_PLAN_DIFF_SCHEMA,
        "status": status,
        "operation_id": operation.id,
        "intent": operation.intent,
        "target": operation.target,
        "mode": operation.mode,
        "catalog_binding": {
            "captured": bool(planned_catalog),
            "fields": catalog_fields,
        },
        "profile_binding": profile_field,
        "environment_binding": environment_field,
        "applicability": applicability,
        "drift_summary": sorted(dict.fromkeys(changed_fields)),
        "safe_next_actions": _safe_next_actions(operation, status),
        "non_claims": [
            "Does not execute work or start operations.",
            "Does not create a replacement plan automatically.",
            "Does not recompute GovEngine policy reasoning.",
        ],
    }


def _resolve_current_catalog_binding(
    operation: Operation,
    plan: OperationPlan,
) -> tuple[dict[str, str] | None, dict[str, Any]]:
    runtime = operation.metadata.get("catalog_runtime")
    if not isinstance(runtime, Mapping) or not plan.catalog_binding:
        return None, {
            "checked": False,
            "applicable": None,
            "status": "",
            "reason_codes": [],
        }
    catalog_path = str(runtime.get("catalog_path") or "").strip()
    target_id = str(runtime.get("target_id") or "").strip()
    if not catalog_path or not target_id:
        return None, {
            "checked": False,
            "applicable": None,
            "status": "catalog_runtime_incomplete",
            "reason_codes": ["catalog_runtime_incomplete"],
        }
    path = Path(catalog_path)
    if not path.is_file():
        return None, {
            "checked": True,
            "applicable": False,
            "status": "catalog_missing",
            "reason_codes": ["catalog_missing"],
        }
    try:
        resolution = CatalogService(path).resolve_operation(target_id, operation.intent)
    except RExecOpValidationError as exc:
        return None, {
            "checked": True,
            "applicable": False,
            "status": "catalog_resolution_failed",
            "reason_codes": [str(exc)],
        }
    applicability = resolution.applicability.as_dict()
    return resolution.binding.as_dict(), {
        "checked": True,
        "applicable": applicability["applicable"],
        "status": applicability["status"],
        "reason_codes": list(applicability["reason_codes"]),
    }


def _compare_binding_fields(
    planned: Mapping[str, str],
    current: Mapping[str, str] | None,
) -> list[dict[str, Any]]:
    if not planned:
        return []
    if current is None:
        return [
            {
                "field": field,
                "planned": planned.get(field, ""),
                "current": "",
                "changed": None,
            }
            for field in CATALOG_BINDING_FIELDS
            if field in planned
        ]
    fields = sorted(set(planned) | set(current))
    return [
        {
            "field": field,
            "planned": planned.get(field, ""),
            "current": current.get(field, ""),
            "changed": planned.get(field, "") != current.get(field, ""),
        }
        for field in fields
        if field in CATALOG_BINDING_FIELDS
    ]


def _compare_digest_field(
    planned: str,
    current: str,
    *,
    captured: bool,
) -> dict[str, Any]:
    if not captured:
        return {
            "planned_digest": "",
            "current_digest": current,
            "changed": None,
            "captured": False,
        }
    return {
        "planned_digest": planned,
        "current_digest": current,
        "changed": planned != current,
        "captured": True,
    }


def _current_profile_digest(operation: Operation) -> str:
    profile_root_raw = str(operation.metadata.get("profile_root") or "").strip()
    if not profile_root_raw:
        return ""
    profile_root = Path(profile_root_raw)
    if not profile_root.exists():
        return ""
    return profile_snapshot_digest(profile_root)


def _current_environment_digest(operation: Operation) -> str:
    environment_path_raw = str(operation.metadata.get("environment_path") or "").strip()
    if not environment_path_raw:
        return ""
    environment_path = Path(environment_path_raw)
    if not environment_path.is_file():
        return ""
    return yaml_document_digest(environment_path)


def _diff_status(
    *,
    changed_fields: list[str],
    applicability: Mapping[str, Any],
    catalog_captured: bool,
    catalog_resolvable: bool,
) -> str:
    if changed_fields:
        return "drifted"
    if applicability.get("checked") and applicability.get("applicable") is False:
        return "drifted"
    if catalog_captured and not catalog_resolvable:
        return "unavailable"
    return "unchanged"


def _safe_next_actions(operation: Operation, status: str) -> list[str]:
    if status == "drifted":
        return [
            "Create a new operation plan for the current profile/env/catalog state.",
            f"rexecop operation review --operation {operation.id}",
            f"rexecop operation explain --operation {operation.id}",
        ]
    if status == "unavailable":
        return [
            f"rexecop operation explain --operation {operation.id}",
            f"rexecop status --operation {operation.id}",
        ]
    return [
        f"rexecop operation review --operation {operation.id}",
        f"rexecop start --operation {operation.id}",
    ]


def render_operation_plan_diff(payload: Mapping[str, Any], fmt: str) -> str:
    normalized = str(fmt or "json").strip().lower()
    if normalized == "json":
        import json

        return json.dumps(dict(payload), indent=2, sort_keys=True)
    if normalized == "table":
        return _render_diff_table(payload)
    if normalized == "markdown":
        return _render_diff_markdown(payload)
    raise ValueError(f"unsupported diff format: {fmt}")


def _render_diff_table(payload: Mapping[str, Any]) -> str:
    lines = [
        f"status          {payload.get('status', '')}",
        f"operation_id    {payload.get('operation_id', '')}",
    ]
    drift = payload.get("drift_summary")
    if isinstance(drift, list) and drift:
        lines.append(f"drift_summary    {', '.join(str(item) for item in drift)}")
    fields = payload.get("catalog_binding", {}).get("fields", [])
    if isinstance(fields, list):
        for item in fields:
            if not isinstance(item, Mapping) or item.get("changed") is not True:
                continue
            lines.append(
                f"{item.get('field', '')}  planned={item.get('planned', '')} "
                f"current={item.get('current', '')}"
            )
    return "\n".join(lines) + "\n"


def _render_diff_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Operation plan diff",
        "",
        f"**Status:** {payload.get('status', '')}",
        f"**Operation:** {payload.get('operation_id', '')}",
        "",
    ]
    drift = payload.get("drift_summary")
    if isinstance(drift, list) and drift:
        lines.extend(["## Drift summary", ""])
        lines.extend(f"- {item}" for item in drift)
        lines.append("")
    fields = payload.get("catalog_binding", {}).get("fields", [])
    changed = [
        item
        for item in fields
        if isinstance(item, Mapping) and item.get("changed") is True
    ]
    if changed:
        lines.extend(
            [
                "## Changed catalog bindings",
                "",
                "| Field | Planned | Current |",
                "| --- | --- | --- |",
            ]
        )
        for item in changed:
            lines.append(
                f"| {item.get('field', '')} | {item.get('planned', '')} | "
                f"{item.get('current', '')} |"
            )
        lines.append("")
    actions = payload.get("safe_next_actions")
    if isinstance(actions, list) and actions:
        lines.extend(["## Safe next actions", ""])
        lines.extend(f"- {item}" for item in actions)
    return "\n".join(lines) + "\n"