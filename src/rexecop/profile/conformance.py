from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from rexecop.catalog.service import compile_profile_operations
from rexecop.errors import RExecOpValidationError
from rexecop.profile.loader import LoadedProfile, load_profile
from rexecop.profile.resolver import resolve_profile_path
from rexecop.reaction.compiler import compile_reaction_pack
from rexecop.workflow.loader import load_workflow

OBSERVATION_SCHEMA_REF = "schemas/observation_envelope.v0.1.schema.json"
READ_ONLY_MODES = frozenset({"read_only", "observe", "dry_run", "emergency_readonly"})
CONFORMANCE_TRACKS = ("all", "readonly", "mutation")
CONFORMANCE_CATEGORIES = (
    "readonly",
    "mutation",
    "reaction",
    "catalog",
    "connector",
    "validation",
)
ConformanceTrack = Literal["all", "readonly", "mutation"]
REACTION_OBSERVATION_KEYS = frozenset(
    {
        "shared_state_key",
        "schema_ref",
        "source_intent",
        "producer_step",
        "requires_completed_operation",
    }
)


@dataclass(frozen=True)
class ConformanceCategoryResult:
    status: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ProfileConformanceResult:
    profile: str
    version: str
    status: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    checked_intents: tuple[str, ...] = ()
    skipped_intents: tuple[str, ...] = ()
    mutation_candidate_intents: tuple[str, ...] = ()
    reaction_observation_intents: tuple[str, ...] = ()
    checked_surfaces: tuple[str, ...] = field(default_factory=tuple)
    track: str = "all"
    categories: tuple[tuple[str, ConformanceCategoryResult], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "version": self.version,
            "status": self.status,
            "track": self.track,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "checked_intents": list(self.checked_intents),
            "skipped_intents": list(self.skipped_intents),
            "mutation_candidate_intents": list(self.mutation_candidate_intents),
            "reaction_observation_intents": list(self.reaction_observation_intents),
            "checked_surfaces": list(self.checked_surfaces),
            "categories": {name: result.as_dict() for name, result in self.categories},
        }


@dataclass
class _CategoryBuckets:
    errors: dict[str, list[str]] = field(
        default_factory=lambda: {name: [] for name in CONFORMANCE_CATEGORIES}
    )
    warnings: dict[str, list[str]] = field(
        default_factory=lambda: {name: [] for name in CONFORMANCE_CATEGORIES}
    )

    def error(self, category: str, message: str) -> None:
        self.errors[category].append(message)

    def warn(self, category: str, message: str) -> None:
        self.warnings[category].append(message)

    def flat_errors(self) -> tuple[str, ...]:
        return tuple(sorted({message for items in self.errors.values() for message in items}))

    def flat_warnings(self) -> tuple[str, ...]:
        return tuple(sorted({message for items in self.warnings.values() for message in items}))

    def build_categories(self, *, track: str) -> tuple[tuple[str, ConformanceCategoryResult], ...]:
        results: list[tuple[str, ConformanceCategoryResult]] = []
        for name in CONFORMANCE_CATEGORIES:
            errors = tuple(sorted(set(self.errors[name])))
            warnings = tuple(sorted(set(self.warnings[name])))
            if errors:
                status = "failed"
            elif name == "mutation" and track == "readonly" and warnings:
                status = "skipped"
            elif warnings:
                status = "warning"
            else:
                status = "passed"
            results.append(
                (
                    name,
                    ConformanceCategoryResult(status=status, errors=errors, warnings=warnings),
                )
            )
        return tuple(results)


def validate_profile_conformance(
    profile_path: str | Path,
    *,
    require_reaction_observation: bool = False,
    require_readonly: bool = False,
    track: ConformanceTrack | str | None = None,
) -> ProfileConformanceResult:
    checked_track = _resolve_track(track, require_readonly=require_readonly)
    profile = load_profile(resolve_profile_path(profile_path))
    buckets = _CategoryBuckets()
    observation_intents: list[str] = []
    checked_intents: list[str] = []
    skipped_intents: list[str] = []
    mutation_candidate_intents: list[str] = []
    checked_surfaces = [
        "profile_contract",
        "operation_catalog_projection",
        "workflow_connector_contracts",
    ]

    try:
        operations = compile_profile_operations(profile)
    except RExecOpValidationError as exc:
        operations = []
        buckets.error("catalog", f"operation_catalog_projection:{exc}")

    for operation in operations:
        metadata = _intent_metadata(profile, operation.id, buckets)
        if not metadata:
            continue
        modes = {str(item) for item in metadata.get("modes") or []}
        readonly_operation = (
            bool(modes)
            and modes <= READ_ONLY_MODES
            and operation.side_effect_class == "none"
        )
        mutation_operation = not readonly_operation
        if mutation_operation:
            mutation_candidate_intents.append(operation.id)
        if checked_track == "readonly" and mutation_operation:
            skipped_intents.append(operation.id)
            buckets.warn(
                "mutation",
                f"{operation.id}:mutation_candidate:modes:{sorted(modes)}:"
                f"side_effect_class:{operation.side_effect_class}",
            )
            continue
        if checked_track == "mutation" and not mutation_operation:
            skipped_intents.append(operation.id)
            buckets.warn("readonly", f"{operation.id}:readonly_candidate_skipped_on_mutation_track")
            continue

        checked_intents.append(operation.id)
        if checked_track == "readonly":
            if not modes or not modes <= READ_ONLY_MODES:
                buckets.error("readonly", f"{operation.id}:non_readonly_modes:{sorted(modes)}")
            if operation.side_effect_class != "none":
                buckets.error(
                    "readonly",
                    f"{operation.id}:side_effect_class:{operation.side_effect_class}",
                )
        elif checked_track == "mutation":
            _validate_mutation_candidate(
                operation_id=operation.id,
                modes=modes,
                side_effect_class=operation.side_effect_class,
                metadata=metadata,
                buckets=buckets,
            )
        _validate_validation_ref(profile, operation.id, metadata, buckets)
        workflow = _workflow(profile, operation.id, buckets)
        if workflow is not None:
            for connector in workflow.required_connectors():
                try:
                    contract = profile.connector_contract(connector)
                except RExecOpValidationError as exc:
                    buckets.error(
                        "connector",
                        f"{operation.id}:connector_contract:{connector}:{exc}",
                    )
                    continue
                if contract is None:
                    buckets.error(
                        "connector",
                        f"{operation.id}:missing_connector_contract:{connector}",
                    )
        declaration = metadata.get("reaction_observation")
        if declaration is not None:
            _validate_reaction_observation_declaration(
                profile=profile,
                intent_id=operation.id,
                declaration=declaration,
                buckets=buckets,
            )
            observation_intents.append(operation.id)

    reaction_path = profile.root / "reactions" / "reaction_pack.yaml"
    if reaction_path.is_file():
        checked_surfaces.append("reaction_pack")
        try:
            compile_reaction_pack(profile, reaction_path)
        except RExecOpValidationError as exc:
            buckets.error("reaction", f"reaction_pack:{exc}")
    else:
        buckets.warn("reaction", "reaction_pack:not_present")

    if require_reaction_observation and not observation_intents:
        buckets.error("reaction", "reaction_observation:not_declared")
    if checked_track == "mutation" and not checked_intents:
        buckets.error("mutation", "mutation_track:no_mutation_candidates")

    errors = buckets.flat_errors()
    return ProfileConformanceResult(
        profile=profile.name,
        version=profile.version,
        status="passed" if not errors else "failed",
        errors=errors,
        warnings=buckets.flat_warnings(),
        checked_intents=tuple(sorted(set(checked_intents))),
        skipped_intents=tuple(sorted(set(skipped_intents))),
        mutation_candidate_intents=tuple(sorted(set(mutation_candidate_intents))),
        reaction_observation_intents=tuple(sorted(set(observation_intents))),
        checked_surfaces=tuple(checked_surfaces),
        track=checked_track,
        categories=buckets.build_categories(track=checked_track),
    )


def _resolve_track(
    track: ConformanceTrack | str | None,
    *,
    require_readonly: bool,
) -> ConformanceTrack:
    if track is None:
        return "readonly" if require_readonly else "all"
    text = str(track).strip().lower()
    if text == "read_only":
        text = "readonly"
    if text not in CONFORMANCE_TRACKS:
        raise RExecOpValidationError(
            f"profile conformance track must be one of: {', '.join(CONFORMANCE_TRACKS)}"
        )
    return text  # type: ignore[return-value]


def _validate_mutation_candidate(
    *,
    operation_id: str,
    modes: set[str],
    side_effect_class: str,
    metadata: dict[str, Any],
    buckets: _CategoryBuckets,
) -> None:
    if not modes:
        buckets.error("mutation", f"{operation_id}:mutation_candidate:modes_missing")
    if modes and modes <= READ_ONLY_MODES:
        buckets.error(
            "mutation",
            f"{operation_id}:mutation_candidate:not_mutating_modes:{sorted(modes)}",
        )
    if side_effect_class == "none":
        buckets.error("mutation", f"{operation_id}:mutation_candidate:side_effect_class:none")
    facts_contract = str(metadata.get("facts_contract") or "").strip()
    if not facts_contract:
        buckets.error("mutation", f"{operation_id}:mutation_candidate:facts_contract_missing")
    catalog = metadata.get("catalog")
    runbook_ref = ""
    validation_ref = ""
    if isinstance(catalog, dict):
        runbook_ref = str(catalog.get("runbook_ref") or "").strip()
        validation_ref = str(catalog.get("validation_ref") or "").strip()
    if not runbook_ref:
        buckets.error("mutation", f"{operation_id}:mutation_candidate:runbook_ref_missing")
    if not validation_ref:
        buckets.error("mutation", f"{operation_id}:mutation_candidate:validation_ref_missing")


def _validate_validation_ref(
    profile: LoadedProfile,
    intent_id: str,
    metadata: dict[str, Any],
    buckets: _CategoryBuckets,
) -> None:
    catalog = metadata.get("catalog")
    if not isinstance(catalog, dict):
        buckets.error("validation", f"{intent_id}:catalog_missing")
        return
    validation_ref = str(catalog.get("validation_ref") or "").strip()
    if not validation_ref:
        buckets.error("validation", f"{intent_id}:validation_ref_missing")
        return
    path = (profile.root / validation_ref).resolve()
    root = profile.root.resolve()
    if root not in path.parents and path != root:
        buckets.error("validation", f"{intent_id}:validation_ref_escapes_profile:{validation_ref}")
        return
    if not path.is_file():
        buckets.error("validation", f"{intent_id}:validation_ref_not_found:{validation_ref}")


def _intent_metadata(
    profile: LoadedProfile,
    intent_id: str,
    buckets: _CategoryBuckets,
) -> dict[str, Any]:
    try:
        return profile.intent_metadata(intent_id)
    except RExecOpValidationError as exc:
        buckets.error("catalog", f"{intent_id}:intent_metadata:{exc}")
        return {}


def _workflow(
    profile: LoadedProfile,
    intent_id: str,
    buckets: _CategoryBuckets,
) -> Any | None:
    try:
        return load_workflow(profile.resolve_workflow_path(intent_id))
    except RExecOpValidationError as exc:
        buckets.error("connector", f"{intent_id}:workflow:{exc}")
        return None


def _validate_reaction_observation_declaration(
    *,
    profile: LoadedProfile,
    intent_id: str,
    declaration: Any,
    buckets: _CategoryBuckets,
) -> None:
    if not isinstance(declaration, dict):
        buckets.error("reaction", f"{intent_id}:reaction_observation:not_mapping")
        return
    unknown = sorted(str(key) for key in declaration if key not in REACTION_OBSERVATION_KEYS)
    if unknown:
        buckets.error(
            "reaction",
            f"{intent_id}:reaction_observation:unknown_keys:{','.join(unknown)}",
        )
    if declaration.get("shared_state_key") != "reaction_observation":
        buckets.error("reaction", f"{intent_id}:reaction_observation:shared_state_key")
    if declaration.get("schema_ref") != OBSERVATION_SCHEMA_REF:
        buckets.error("reaction", f"{intent_id}:reaction_observation:schema_ref")
    if declaration.get("source_intent") != intent_id:
        buckets.error("reaction", f"{intent_id}:reaction_observation:source_intent")
    if declaration.get("requires_completed_operation") is not True:
        buckets.error("reaction", f"{intent_id}:reaction_observation:requires_completed_operation")
    producer_step = str(declaration.get("producer_step") or "").strip()
    if not producer_step:
        buckets.error("reaction", f"{intent_id}:reaction_observation:producer_step")
        return
    workflow = _workflow(profile, intent_id, buckets)
    if workflow is None:
        return
    steps = {step.id: step for step in workflow.steps}
    step = steps.get(producer_step)
    if step is None:
        buckets.error("reaction", f"{intent_id}:reaction_observation:producer_step_not_found")
    elif step.type != "internal":
        buckets.error("reaction", f"{intent_id}:reaction_observation:producer_step_not_internal")