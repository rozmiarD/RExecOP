from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rexecop.catalog.digest import canonical_digest
from rexecop.errors import RExecOpValidationError
from rexecop.observability.failure_classes import FAILURE_CLASSES
from rexecop.profile.loader import LoadedProfile
from rexecop.yaml_input import load_yaml_file

OPERATOR_METADATA_SCHEMA = "rexecop.profile_operator_metadata.v0.1"
OPERATION_PROFILE_EXPLAIN_SCHEMA = "rexecop.operation_profile_explain.v0.1"
OPERATOR_METADATA_FILENAME = "operator_metadata.yaml"
SUPPORTED_SCHEMA_VERSION = "v0.1"
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

INTENT_KEYS = frozenset(
    {
        "label",
        "summary",
        "runbook_hint",
        "safe_next_options",
        "failure_mapping",
    }
)

FAILURE_MAPPING_KEYS = frozenset({"operator_summary", "runbook_hint", "safe_next_options"})


@dataclass(frozen=True)
class OperatorMetadataCoverage:
    status: str
    schema_version: str
    intent_count: int
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "schema_version": self.schema_version,
            "intent_count": self.intent_count,
            "errors": list(self.errors),
        }


def operator_metadata_path(profile_root: Path) -> Path:
    return profile_root / OPERATOR_METADATA_FILENAME


def load_operator_metadata(profile: LoadedProfile) -> dict[str, Any] | None:
    path = operator_metadata_path(profile.root)
    if not path.is_file():
        return None
    data = load_yaml_file(path)
    if not isinstance(data, dict):
        raise RExecOpValidationError(f"invalid operator metadata yaml: {path}")
    document = data.get("operator_metadata")
    if not isinstance(document, dict):
        raise RExecOpValidationError(f"operator_metadata mapping missing: {path}")
    return document


def collect_operator_metadata_errors(profile: LoadedProfile) -> list[str]:
    path = operator_metadata_path(profile.root)
    if not path.is_file():
        return []
    try:
        document = load_operator_metadata(profile)
    except RExecOpValidationError as exc:
        return [f"operator_metadata:{exc}"]
    if document is None:
        return ["operator_metadata:missing_mapping"]
    return _validate_operator_metadata_document(profile, document)


def evaluate_operator_metadata_coverage(profile: LoadedProfile) -> OperatorMetadataCoverage:
    errors = collect_operator_metadata_errors(profile)
    path = operator_metadata_path(profile.root)
    if not path.is_file():
        return OperatorMetadataCoverage(
            status="missing",
            schema_version="",
            intent_count=0,
            errors=tuple(errors),
        )
    try:
        document = load_operator_metadata(profile)
    except RExecOpValidationError:
        return OperatorMetadataCoverage(
            status="failed",
            schema_version="",
            intent_count=0,
            errors=tuple(errors),
        )
    intents = document.get("intents") if isinstance(document, dict) else {}
    intent_count = len(intents) if isinstance(intents, dict) else 0
    schema_version = str((document or {}).get("schema_version") or "")
    status = "passed" if not errors else "failed"
    return OperatorMetadataCoverage(
        status=status,
        schema_version=schema_version,
        intent_count=intent_count,
        errors=tuple(errors),
    )


def intent_operator_metadata(profile: LoadedProfile, intent_id: str) -> dict[str, Any] | None:
    document = load_operator_metadata(profile)
    if document is None:
        return None
    intents = document.get("intents")
    if not isinstance(intents, dict):
        return None
    entry = intents.get(intent_id)
    if not isinstance(entry, dict):
        return None
    return _intent_projection(profile, intent_id, entry)


def explain_profile_operation(profile: LoadedProfile, intent_id: str) -> dict[str, Any]:
    from rexecop.catalog.service import compile_operation_descriptor

    operation = compile_operation_descriptor(profile, intent_id)
    operator = intent_operator_metadata(profile, intent_id)
    payload: dict[str, Any] = {
        "schema": OPERATION_PROFILE_EXPLAIN_SCHEMA,
        "operation": operation.as_dict(),
        "non_claims": [
            "Profile-owned operator guidance only.",
            "Does not execute work or override GovEngine admission.",
            "Technical applicability still requires catalog and policy gates.",
        ],
    }
    if operator is not None:
        payload["operator_metadata"] = operator
    return payload


def merge_profile_safe_next_options(
    profile: LoadedProfile,
    intent_id: str,
    base_options: list[str],
) -> list[str]:
    operator = intent_operator_metadata(profile, intent_id)
    if operator is None:
        return list(base_options)
    profile_options = operator.get("safe_next_options") or []
    if not isinstance(profile_options, list):
        return list(base_options)
    merged: list[str] = []
    seen: set[str] = set()
    for option in [*profile_options, *base_options]:
        text = str(option or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)
    return merged


def resolve_failure_operator_hints(
    profile: LoadedProfile,
    intent_id: str,
    failure_class: str,
) -> dict[str, Any]:
    operator = intent_operator_metadata(profile, intent_id)
    if operator is None:
        return {}
    failure_mapping = operator.get("failure_mapping")
    if not isinstance(failure_mapping, dict):
        return {}
    entry = failure_mapping.get(failure_class)
    if not isinstance(entry, dict):
        return {}
    hints: dict[str, Any] = {}
    summary = str(entry.get("operator_summary") or "").strip()
    if summary:
        hints["operator_summary"] = summary
    runbook_hint = str(entry.get("runbook_hint") or "").strip()
    if runbook_hint:
        hints["runbook_hint"] = runbook_hint
    options = entry.get("safe_next_options")
    if isinstance(options, list):
        cleaned = [str(item).strip() for item in options if str(item).strip()]
        if cleaned:
            hints["safe_next_options"] = cleaned
    return hints


def _validate_operator_metadata_document(
    profile: LoadedProfile,
    document: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    schema_version = str(document.get("schema_version") or "").strip()
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        errors.append(
            f"operator_metadata:unsupported_schema_version:{schema_version or 'missing'}"
        )

    profile_meta = document.get("profile")
    if not isinstance(profile_meta, dict):
        errors.append("operator_metadata.profile:missing_mapping")
    else:
        label = str(profile_meta.get("label") or "").strip()
        if not label:
            errors.append("operator_metadata.profile.label:required")
        elif len(label) > 120:
            errors.append("operator_metadata.profile.label:too_long")

    intents = document.get("intents")
    if not isinstance(intents, dict) or not intents:
        errors.append("operator_metadata.intents:required")
        return sorted(set(errors))

    declared_intents = {path.stem for path in (profile.root / "intents").glob("*.yaml")}
    unknown_intents = sorted(set(intents) - declared_intents)
    missing_intents = sorted(declared_intents - set(intents))
    if unknown_intents:
        errors.append(f"operator_metadata.intents.unknown:{','.join(unknown_intents)}")
    if missing_intents:
        errors.append(f"operator_metadata.intents.missing:{','.join(missing_intents)}")

    for intent_id, entry in sorted(intents.items()):
        if not isinstance(entry, dict):
            errors.append(f"{intent_id}:operator_metadata:not_mapping")
            continue
        unknown = sorted(str(key) for key in entry if key not in INTENT_KEYS)
        if unknown:
            errors.append(f"{intent_id}:operator_metadata.unknown_keys:{','.join(unknown)}")
        label = str(entry.get("label") or "").strip()
        if not label:
            errors.append(f"{intent_id}:operator_metadata.label:required")
        elif len(label) > 120:
            errors.append(f"{intent_id}:operator_metadata.label:too_long")
        summary = str(entry.get("summary") or "").strip()
        if summary and len(summary) > 500:
            errors.append(f"{intent_id}:operator_metadata.summary:too_long")
        runbook_hint = str(entry.get("runbook_hint") or "").strip()
        if not runbook_hint:
            errors.append(f"{intent_id}:operator_metadata.runbook_hint:required")
        elif len(runbook_hint) > 500:
            errors.append(f"{intent_id}:operator_metadata.runbook_hint:too_long")
        errors.extend(
            _validate_option_list(intent_id, "safe_next_options", entry.get("safe_next_options"))
        )
        failure_mapping = entry.get("failure_mapping")
        if not isinstance(failure_mapping, dict) or not failure_mapping:
            errors.append(f"{intent_id}:operator_metadata.failure_mapping:required")
            continue
        unknown_classes = sorted(
            str(key) for key in failure_mapping if str(key) not in FAILURE_CLASSES
        )
        if unknown_classes:
            errors.append(
                f"{intent_id}:operator_metadata.failure_mapping.unknown:{','.join(unknown_classes)}"
            )
        for failure_class, mapping in sorted(failure_mapping.items()):
            if failure_class not in FAILURE_CLASSES:
                continue
            if not isinstance(mapping, dict):
                errors.append(f"{intent_id}:failure_mapping.{failure_class}:not_mapping")
                continue
            unknown_mapping = sorted(
                str(key) for key in mapping if key not in FAILURE_MAPPING_KEYS
            )
            if unknown_mapping:
                errors.append(
                    f"{intent_id}:failure_mapping.{failure_class}.unknown:"
                    f"{','.join(unknown_mapping)}"
                )
            summary_text = str(mapping.get("operator_summary") or "").strip()
            if not summary_text:
                errors.append(
                    f"{intent_id}:failure_mapping.{failure_class}.operator_summary:required"
                )
            elif len(summary_text) > 500:
                errors.append(
                    f"{intent_id}:failure_mapping.{failure_class}.operator_summary:too_long"
                )
            hint_text = str(mapping.get("runbook_hint") or "").strip()
            if hint_text and len(hint_text) > 500:
                errors.append(
                    f"{intent_id}:failure_mapping.{failure_class}.runbook_hint:too_long"
                )
            errors.extend(
                _validate_option_list(
                    intent_id,
                    f"failure_mapping.{failure_class}.safe_next_options",
                    mapping.get("safe_next_options"),
                )
            )

    try:
        from rexecop.catalog.service import compile_profile_operations

        compile_profile_operations(profile)
    except RExecOpValidationError as exc:
        errors.append(f"operator_metadata.catalog_projection:{exc}")

    return sorted(set(errors))


def _validate_option_list(intent_id: str, field: str, value: Any) -> list[str]:
    if value is None:
        return [f"{intent_id}:operator_metadata.{field}:required"]
    if not isinstance(value, list) or not value:
        return [f"{intent_id}:operator_metadata.{field}:required"]
    errors: list[str] = []
    if len(value) > 8:
        errors.append(f"{intent_id}:operator_metadata.{field}:too_many")
    for item in value:
        text = str(item or "").strip()
        if not text:
            errors.append(f"{intent_id}:operator_metadata.{field}:empty_entry")
        elif len(text) > 240:
            errors.append(f"{intent_id}:operator_metadata.{field}:entry_too_long")
    return errors


def _intent_projection(
    profile: LoadedProfile,
    intent_id: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    from rexecop.catalog.service import compile_operation_descriptor

    descriptor = compile_operation_descriptor(profile, intent_id)
    payload = {
        "schema": OPERATOR_METADATA_SCHEMA,
        "intent": intent_id,
        "label": str(entry.get("label") or descriptor.title),
        "summary": str(entry.get("summary") or descriptor.summary),
        "runbook_hint": str(entry.get("runbook_hint") or ""),
        "runbook_ref": descriptor.runbook_ref,
        "safe_next_options": [
            str(item).strip() for item in entry.get("safe_next_options") or [] if str(item).strip()
        ],
        "failure_mapping": _project_failure_mapping(entry.get("failure_mapping")),
        "digest": canonical_digest(
            {
                "intent": intent_id,
                "label": str(entry.get("label") or descriptor.title),
                "summary": str(entry.get("summary") or descriptor.summary),
                "runbook_hint": str(entry.get("runbook_hint") or ""),
            }
        ),
    }
    return payload


def _project_failure_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    projected: dict[str, Any] = {}
    for failure_class in sorted(value):
        if failure_class not in FAILURE_CLASSES:
            continue
        mapping = value[failure_class]
        if not isinstance(mapping, dict):
            continue
        entry: dict[str, Any] = {}
        summary = str(mapping.get("operator_summary") or "").strip()
        if summary:
            entry["operator_summary"] = summary
        runbook_hint = str(mapping.get("runbook_hint") or "").strip()
        if runbook_hint:
            entry["runbook_hint"] = runbook_hint
        options = mapping.get("safe_next_options")
        if isinstance(options, list):
            cleaned = [str(item).strip() for item in options if str(item).strip()]
            if cleaned:
                entry["safe_next_options"] = cleaned
        if entry:
            projected[failure_class] = entry
    return projected
