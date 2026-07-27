from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sclite.artifacts import artifact_sha256

from rexecop.errors import RExecOpValidationError
from rexecop.profile.loader import LoadedProfile
from rexecop.reaction.model import ReactionCondition, ReactionPack, ReactionRule
from rexecop.yaml_input import load_yaml_file

MAX_PACK_BYTES = 262_144
MAX_RULES = 128
MAX_CONDITIONS = 16
OPERATORS = frozenset({"equals", "not_equals", "in", "exists", "gt", "gte", "lt", "lte"})
OUTCOMES = frozenset({"run_intent", "retry_intent", "escalate", "no_op"})
SEVERITIES = frozenset({"info", "low", "medium", "high", "critical"})
PACK_KEYS = frozenset({"id", "version", "budgets", "rules", "fallback"})
RULE_KEYS = frozenset({"id", "priority", "when", "finding", "outcome", "intent_ref"})
CONDITION_KEYS = frozenset({"path", "operator", "value"})
FINDING_KEYS = frozenset({"kind", "severity", "summary"})


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RExecOpValidationError(f"{label} must be a mapping")
    return value


def _strict_keys(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    extras = sorted(set(value) - allowed)
    if extras:
        raise RExecOpValidationError(f"{label} has unsupported fields: {extras}")


def _text(value: Any, label: str, *, maximum: int = 128) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise RExecOpValidationError(f"{label} must be 1..{maximum} characters")
    return result


def _read_only_intent(profile: LoadedProfile, intent_ref: str) -> None:
    metadata = profile.intent_metadata(intent_ref)
    modes = metadata.get("modes")
    if not isinstance(modes, list) or not modes:
        raise RExecOpValidationError(f"reaction intent must declare read-only modes: {intent_ref}")
    if any(
        str(mode) not in {"observe", "dry_run", "emergency_readonly", "read_only"} for mode in modes
    ):
        raise RExecOpValidationError(
            f"reaction intent may not declare mutating modes: {intent_ref}"
        )


def _condition(raw: Any, label: str) -> ReactionCondition:
    item = _mapping(raw, label)
    _strict_keys(item, CONDITION_KEYS, label)
    path = _text(item.get("path"), f"{label}.path", maximum=256)
    if not path.startswith("facts.") or ".." in path:
        raise RExecOpValidationError(f"{label}.path must start with facts.")
    operator = _text(item.get("operator"), f"{label}.operator", maximum=32)
    if operator not in OPERATORS:
        raise RExecOpValidationError(f"{label}.operator is unsupported: {operator}")
    if operator != "exists" and "value" not in item:
        raise RExecOpValidationError(f"{label}.value is required")
    if operator == "in" and not isinstance(item.get("value"), list):
        raise RExecOpValidationError(f"{label}.value must be a list for in")
    if operator in {"gt", "gte", "lt", "lte"} and not isinstance(item.get("value"), (int, float)):
        raise RExecOpValidationError(f"{label}.value must be numeric")
    return ReactionCondition(path=path, operator=operator, value=item.get("value"))


def _rule(raw: Any, label: str, profile: LoadedProfile, *, fallback: bool = False) -> ReactionRule:
    item = _mapping(raw, label)
    _strict_keys(item, RULE_KEYS, label)
    rule_id = _text(item.get("id"), f"{label}.id")
    priority = int(item.get("priority", 0))
    conditions_raw = item.get("when", [])
    if not isinstance(conditions_raw, list) or (not conditions_raw and not fallback):
        raise RExecOpValidationError(f"{label}.when must be a non-empty list")
    if len(conditions_raw) > MAX_CONDITIONS:
        raise RExecOpValidationError(f"{label}.when exceeds {MAX_CONDITIONS} conditions")
    conditions = tuple(
        _condition(value, f"{label}.when[{index}]") for index, value in enumerate(conditions_raw)
    )
    finding = _mapping(item.get("finding"), f"{label}.finding")
    _strict_keys(finding, FINDING_KEYS, f"{label}.finding")
    kind = _text(finding.get("kind"), f"{label}.finding.kind")
    severity = _text(finding.get("severity"), f"{label}.finding.severity", maximum=16)
    if severity not in SEVERITIES:
        raise RExecOpValidationError(f"{label}.finding.severity is unsupported: {severity}")
    summary = _text(finding.get("summary"), f"{label}.finding.summary", maximum=512)
    outcome = _text(item.get("outcome"), f"{label}.outcome", maximum=32)
    if outcome not in OUTCOMES:
        raise RExecOpValidationError(f"{label}.outcome is unsupported: {outcome}")
    intent_ref = str(item.get("intent_ref") or "").strip() or None
    if outcome in {"run_intent", "retry_intent"}:
        if intent_ref is None:
            raise RExecOpValidationError(f"{label}.intent_ref is required for {outcome}")
        _read_only_intent(profile, intent_ref)
    elif intent_ref is not None:
        raise RExecOpValidationError(f"{label}.intent_ref is not allowed for {outcome}")
    canonical = {
        "id": rule_id,
        "priority": priority,
        "when": [condition.as_dict() for condition in conditions],
        "finding": {"kind": kind, "severity": severity, "summary": summary},
        "outcome": outcome,
        "intent_ref": intent_ref,
    }
    return ReactionRule(
        rule_id=rule_id,
        priority=priority,
        conditions=conditions,
        finding_kind=kind,
        finding_severity=severity,
        finding_summary=summary,
        outcome=outcome,
        intent_ref=intent_ref,
        digest=artifact_sha256(canonical),
    )


def compile_reaction_pack(profile: LoadedProfile, path: Path | None = None) -> ReactionPack:
    pack_path = path or profile.root / "reactions" / "reaction_pack.yaml"
    if not pack_path.is_file():
        raise RExecOpValidationError(f"reaction pack not found: {pack_path}")
    document = load_yaml_file(pack_path, max_bytes=MAX_PACK_BYTES)
    root = _mapping(document, "reaction_pack document")
    pack = _mapping(root.get("reaction_pack"), "reaction_pack")
    _strict_keys(pack, PACK_KEYS, "reaction_pack")
    pack_id = _text(pack.get("id"), "reaction_pack.id")
    version = _text(pack.get("version"), "reaction_pack.version", maximum=64)
    budgets = _mapping(pack.get("budgets"), "reaction_pack.budgets")
    _strict_keys(budgets, frozenset({"max_depth", "max_reactions"}), "reaction_pack.budgets")
    max_depth = int(budgets.get("max_depth", 0))
    max_reactions = int(budgets.get("max_reactions", 0))
    if not 1 <= max_depth <= 32 or not 1 <= max_reactions <= 1024:
        raise RExecOpValidationError("reaction budgets are outside bounded limits")
    rules_raw = pack.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw or len(rules_raw) > MAX_RULES:
        raise RExecOpValidationError(f"reaction_pack.rules must contain 1..{MAX_RULES} rules")
    rules = tuple(
        _rule(value, f"reaction_pack.rules[{index}]", profile)
        for index, value in enumerate(rules_raw)
    )
    if len({rule.rule_id for rule in rules}) != len(rules):
        raise RExecOpValidationError("reaction rule ids must be unique")
    fingerprints: dict[str, str] = {}
    for rule in rules:
        fingerprint = artifact_sha256([condition.as_dict() for condition in rule.conditions])
        previous = fingerprints.get(fingerprint)
        if previous is not None:
            raise RExecOpValidationError(
                f"ambiguous reaction conditions: {previous}, {rule.rule_id}"
            )
        fingerprints[fingerprint] = rule.rule_id
    fallback = _rule(pack.get("fallback"), "reaction_pack.fallback", profile, fallback=True)
    ordered = tuple(sorted(rules, key=lambda item: (item.priority, item.rule_id)))
    canonical_pack = dict(pack)
    pack_digest = artifact_sha256(canonical_pack)
    profile_digest = artifact_sha256(
        {
            "contract": profile.contract,
            "reaction_pack_digest": pack_digest,
            "intents": {
                rule.intent_ref: profile.intent_metadata(rule.intent_ref)
                for rule in (*ordered, fallback)
                if rule.intent_ref
            },
        }
    )
    return ReactionPack(
        pack_id=pack_id,
        version=version,
        max_depth=max_depth,
        max_reactions=max_reactions,
        rules=ordered,
        fallback=fallback,
        digest=pack_digest,
        profile_digest=profile_digest,
    )
