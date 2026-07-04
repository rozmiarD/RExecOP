from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rexecop.catalog.digest import (
    canonical_digest,
    profile_snapshot_digest,
    yaml_document_digest,
)
from rexecop.catalog.loader import load_catalog_document
from rexecop.catalog.model import (
    ApplicabilityResult,
    CatalogBinding,
    OperationDescriptor,
    TargetDescriptor,
)
from rexecop.catalog.unavailable import build_unavailable_operations_report
from rexecop.errors import RExecOpValidationError
from rexecop.profile.loader import LoadedProfile, load_profile
from rexecop.profile.resolver import resolve_profile_path
from rexecop.workflow.loader import load_workflow

TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
CATALOG_METADATA_KEYS = frozenset(
    {
        "title",
        "summary",
        "target_kinds",
        "required_capabilities",
        "side_effect_class",
        "validation_ref",
        "runbook_ref",
    }
)


@dataclass(frozen=True)
class ResolvedCatalogOperation:
    target: TargetDescriptor
    operation: OperationDescriptor
    applicability: ApplicabilityResult
    binding: CatalogBinding


class CatalogService:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        version, entries, digest = load_catalog_document(self.path)
        self.version = version
        self.digest = digest
        self._targets = {
            str(item["id"]): TargetDescriptor(
                id=str(item["id"]),
                target_kind=str(item["target_kind"]),
                profile_ref=str(item["profile_ref"]),
                environment_id=str(item["environment_id"]),
                environment_target=str(item["environment_target"]),
                capabilities=tuple(item["capabilities"]),
                connector_refs=tuple(item["connector_refs"]),
                classification=dict(item["classification"]),
                environment_path=Path(item["environment_path"]),
                profile_path=Path(item["profile_path"]),
            )
            for item in entries
        }

    def list_targets(self) -> list[dict[str, Any]]:
        return [self._targets[key].public_dict() for key in sorted(self._targets)]

    def get_target(self, target_id: str) -> TargetDescriptor:
        item = self._targets.get(str(target_id).strip())
        if item is None:
            raise RExecOpValidationError(f"unknown catalog target: {target_id}")
        return item

    def list_operations_for_profile(
        self,
        profile_ref: str | Path,
    ) -> list[OperationDescriptor]:
        profile = load_profile(resolve_profile_path(profile_ref))
        return compile_profile_operations(profile)

    def list_operations_for_target(self, target_id: str) -> list[dict[str, Any]]:
        target = self.get_target(target_id)
        profile = load_profile(target.profile_path)
        results: list[dict[str, Any]] = []
        for operation in compile_profile_operations(profile):
            applicability = evaluate_applicability(target, operation)
            results.append(
                {
                    "operation": operation.as_dict(),
                    "applicability": applicability.as_dict(),
                }
            )
        return results

    def list_unavailable_operations_for_target(
        self,
        target_id: str,
        *,
        intent: str | None = None,
    ) -> dict[str, Any]:
        return build_unavailable_operations_report(self, target_id, intent=intent)

    def resolve_operation(
        self,
        target_id: str,
        intent_id: str,
    ) -> ResolvedCatalogOperation:
        target = self.get_target(target_id)
        profile = load_profile(target.profile_path)
        operation = compile_operation_descriptor(profile, intent_id)
        applicability = evaluate_applicability(target, operation)
        binding = CatalogBinding(
            catalog_version=self.version,
            catalog_digest=self.digest,
            target_descriptor_digest=canonical_digest(target.public_dict()),
            operation_descriptor_digest=operation.digest,
            profile_digest=profile_snapshot_digest(profile.root),
            environment_digest=yaml_document_digest(target.environment_path),
            target_id=target.id,
            environment_id=target.environment_id,
            environment_target=target.environment_target,
            profile_ref=target.profile_ref,
        )
        return ResolvedCatalogOperation(
            target=target,
            operation=operation,
            applicability=applicability,
            binding=binding,
        )


def compile_profile_operations(profile: LoadedProfile) -> list[OperationDescriptor]:
    operations: list[OperationDescriptor] = []
    for path in sorted((profile.root / "intents").glob("*.yaml")):
        operations.append(compile_operation_descriptor(profile, path.stem))
    if not operations:
        raise RExecOpValidationError(f"profile has no intents: {profile.name}")
    return operations


def compile_operation_descriptor(
    profile: LoadedProfile,
    intent_id: str,
) -> OperationDescriptor:
    metadata = profile.intent_metadata(intent_id)
    declared_id = _token(metadata.get("id"), "intent.id")
    if declared_id != intent_id:
        raise RExecOpValidationError(
            f"intent file/id mismatch: expected {intent_id}, got {declared_id}"
        )
    catalog = metadata.get("catalog")
    if not isinstance(catalog, dict):
        raise RExecOpValidationError(f"intent catalog metadata missing: {intent_id}")
    unknown = sorted(str(key) for key in catalog if key not in CATALOG_METADATA_KEYS)
    if unknown:
        raise RExecOpValidationError(
            f"unknown intent catalog fields for {intent_id}: {', '.join(unknown)}"
        )
    title = _bounded_text(catalog.get("title"), f"{intent_id}.catalog.title", 120)
    summary = _bounded_text(catalog.get("summary"), f"{intent_id}.catalog.summary", 500)
    target_kinds = _tokens(catalog.get("target_kinds"), f"{intent_id}.catalog.target_kinds")
    required_capabilities = _tokens(
        catalog.get("required_capabilities"),
        f"{intent_id}.catalog.required_capabilities",
        allow_empty=True,
    )
    side_effect_class = _token(
        catalog.get("side_effect_class"),
        f"{intent_id}.catalog.side_effect_class",
    )
    validation_ref = _bounded_text(
        catalog.get("validation_ref"),
        f"{intent_id}.catalog.validation_ref",
        512,
    )
    _resolve_profile_file(profile, validation_ref, f"{intent_id}.catalog.validation_ref")
    runbook_ref = _bounded_text(
        catalog.get("runbook_ref"),
        f"{intent_id}.catalog.runbook_ref",
        512,
    )
    modes = _tokens(metadata.get("modes"), f"{intent_id}.modes")
    workflow = load_workflow(profile.resolve_workflow_path(intent_id))
    required_connectors = tuple(sorted(workflow.required_connectors()))
    payload: dict[str, Any] = {
        "id": declared_id,
        "title": title,
        "summary": summary,
        "profile_ref": profile.name,
        "profile_version": profile.version,
        "target_kinds": list(target_kinds),
        "required_capabilities": list(required_capabilities),
        "required_connectors": list(required_connectors),
        "modes": list(modes),
        "risk": str(metadata.get("risk") or workflow.risk),
        "side_effect_class": side_effect_class,
        "validation_ref": validation_ref,
        "runbook_ref": runbook_ref,
    }
    return OperationDescriptor(
        id=declared_id,
        title=title,
        summary=summary,
        profile_ref=profile.name,
        profile_version=profile.version,
        target_kinds=target_kinds,
        required_capabilities=required_capabilities,
        required_connectors=required_connectors,
        modes=modes,
        risk=str(payload["risk"]),
        side_effect_class=side_effect_class,
        validation_ref=validation_ref,
        runbook_ref=runbook_ref,
        digest=canonical_digest(payload),
    )


def evaluate_applicability(
    target: TargetDescriptor,
    operation: OperationDescriptor,
) -> ApplicabilityResult:
    if target.profile_ref != operation.profile_ref:
        return ApplicabilityResult(
            target_id=target.id,
            operation_id=operation.id,
            applicable=False,
            status="unsupported_profile",
            reason_codes=("profile_mismatch",),
        )
    if target.target_kind not in operation.target_kinds:
        return ApplicabilityResult(
            target_id=target.id,
            operation_id=operation.id,
            applicable=False,
            status="unsupported_target_kind",
            reason_codes=("target_kind_not_declared",),
        )
    missing_capabilities = tuple(
        sorted(set(operation.required_capabilities) - set(target.capabilities))
    )
    if missing_capabilities:
        return ApplicabilityResult(
            target_id=target.id,
            operation_id=operation.id,
            applicable=False,
            status="missing_capability",
            reason_codes=("required_capability_missing",),
            missing_capabilities=missing_capabilities,
        )
    missing_connectors = tuple(
        sorted(set(operation.required_connectors) - set(target.connector_refs))
    )
    if missing_connectors:
        return ApplicabilityResult(
            target_id=target.id,
            operation_id=operation.id,
            applicable=False,
            status="missing_connector",
            reason_codes=("required_connector_missing",),
            missing_connectors=missing_connectors,
        )
    return ApplicabilityResult(
        target_id=target.id,
        operation_id=operation.id,
        applicable=True,
        status="admission_required",
        reason_codes=("technical_requirements_satisfied", "govengine_admission_required"),
    )


def _resolve_profile_file(profile: LoadedProfile, ref: str, field: str) -> Path:
    root = profile.root.resolve()
    path = (root / ref).resolve()
    if root not in path.parents or not path.is_file():
        raise RExecOpValidationError(f"profile file reference invalid at {field}")
    return path


def _bounded_text(value: Any, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise RExecOpValidationError(f"bounded text required at {field}")
    return text


def _token(value: Any, field: str) -> str:
    text = _bounded_text(value, field, 128)
    if not TOKEN.fullmatch(text):
        raise RExecOpValidationError(f"invalid token at {field}")
    return text


def _tokens(value: Any, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        requirement = "list" if allow_empty else "non-empty list"
        raise RExecOpValidationError(f"{requirement} required at {field}")
    result = tuple(sorted({_token(item, f"{field}[]") for item in value}))
    if len(result) != len(value):
        raise RExecOpValidationError(f"duplicate values forbidden at {field}")
    return result
