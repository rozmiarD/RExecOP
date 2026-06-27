from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from govengine import (
    GovApiError,
    TriggerPlanningRequest,
    admit_trigger_planning,
    trigger_planning_admission_digest,
    trigger_planning_request_digest,
)

from rexecop.catalog.digest import canonical_digest
from rexecop.errors import RExecOpValidationError
from rexecop.evidence.event import EvidenceEventType
from rexecop.operation.controller import OperationController
from rexecop.profile.loader import LoadedProfile, load_profile
from rexecop.profile.resolver import resolve_profile_path
from rexecop.storage.atomic import atomic_write_text, secure_directory

TRIGGER_RULES_RELATIVE_PATH = Path("triggers") / "trigger_rules.yaml"
MAX_EVENT_BYTES = 256 * 1024
MAX_FUTURE_SKEW_SECONDS = 300
MAX_PAST_SKEW_SECONDS = 86_400
ALLOWED_DECISIONS = frozenset({"plan_operation", "ignore", "escalate"})
ALLOWED_OPERATORS = frozenset({"exists", "equals", "not_equals", "in"})
ALLOWED_RULE_KEYS = frozenset(
    {"id", "priority", "event_type", "when", "decision", "operation", "cooldown_seconds"}
)
ALLOWED_OPERATION_KEYS = frozenset(
    {
        "intent",
        "target",
        "target_from",
        "catalog_target",
        "catalog_target_from",
        "mode",
        "auto_react",
    }
)
ALLOWED_EVENT_KEYS = frozenset(
    {
        "id",
        "source",
        "type",
        "subject",
        "occurred_at",
        "payload",
        "dedupe_key",
        "cooldown_key",
        "rule_set",
    }
)
_MISSING = object()


@dataclass(frozen=True)
class TriggerRule:
    rule_id: str
    priority: int
    event_type: str
    conditions: tuple[dict[str, Any], ...]
    decision: str
    operation: dict[str, Any]
    cooldown_seconds: int
    digest: str


@dataclass(frozen=True)
class TriggerRuleSet:
    rule_set_id: str
    version: str
    rules: tuple[TriggerRule, ...]
    digest: str


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RExecOpValidationError(f"invalid trigger timestamp: {field}") from exc
    if parsed.tzinfo is None:
        raise RExecOpValidationError(f"trigger timestamp must be timezone-aware: {field}")
    return parsed.astimezone(UTC)


def _event_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    event = payload.get("trigger_event") if "trigger_event" in payload else payload
    if not isinstance(event, Mapping):
        raise RExecOpValidationError("trigger event must be a mapping")
    rendered = _canonical_json(event)
    if len(rendered.encode("utf-8")) > MAX_EVENT_BYTES:
        raise RExecOpValidationError("trigger event exceeds size limit")
    unknown = sorted(str(key) for key in event if key not in ALLOWED_EVENT_KEYS)
    if unknown:
        raise RExecOpValidationError(f"unknown trigger event fields: {', '.join(unknown)}")
    required = ("id", "source", "type", "subject", "occurred_at", "payload")
    for key in required:
        if key not in event:
            raise RExecOpValidationError(f"trigger event missing required field: {key}")
    nested_payload = event.get("payload")
    if not isinstance(nested_payload, Mapping):
        raise RExecOpValidationError("trigger event payload must be a mapping")
    return dict(event)


def _resolve_path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _condition_matches(condition: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    path = str(condition.get("path") or "").strip()
    operator = str(condition.get("operator") or "").strip()
    if not path or operator not in ALLOWED_OPERATORS:
        raise RExecOpValidationError("invalid trigger rule condition")
    actual = _resolve_path(event, path)
    if operator == "exists":
        return actual is not _MISSING
    if actual is _MISSING:
        return False
    expected = condition.get("value")
    if operator == "equals":
        return actual == expected
    if operator == "not_equals":
        return actual != expected
    if operator == "in":
        if not isinstance(expected, list):
            raise RExecOpValidationError("trigger condition operator 'in' requires a list")
        return actual in expected
    return False


def _load_trigger_rules(profile: LoadedProfile) -> TriggerRuleSet:
    path = profile.root / TRIGGER_RULES_RELATIVE_PATH
    if not path.is_file():
        raise RExecOpValidationError("profile trigger rules not found")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RExecOpValidationError("invalid profile trigger rules") from exc
    if not isinstance(document, Mapping):
        raise RExecOpValidationError("trigger rules document must be a mapping")
    raw = document.get("trigger_rules")
    if not isinstance(raw, Mapping):
        raise RExecOpValidationError("trigger_rules mapping required")
    rule_set_id = str(raw.get("id") or "").strip()
    version = str(raw.get("version") or "").strip()
    rules_raw = raw.get("rules")
    if not rule_set_id or not version:
        raise RExecOpValidationError("trigger rule set id and version are required")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise RExecOpValidationError("trigger rule set requires non-empty rules")
    rules: list[TriggerRule] = []
    seen: set[str] = set()
    for item in rules_raw:
        if not isinstance(item, Mapping):
            raise RExecOpValidationError("trigger rule must be a mapping")
        unknown = sorted(str(key) for key in item if key not in ALLOWED_RULE_KEYS)
        if unknown:
            raise RExecOpValidationError(f"unknown trigger rule fields: {', '.join(unknown)}")
        rule_id = str(item.get("id") or "").strip()
        if not rule_id or rule_id in seen:
            raise RExecOpValidationError("trigger rule id must be unique")
        seen.add(rule_id)
        event_type = str(item.get("event_type") or "").strip()
        decision = str(item.get("decision") or "").strip()
        if not event_type:
            raise RExecOpValidationError(f"trigger rule event_type required: {rule_id}")
        if decision not in ALLOWED_DECISIONS:
            raise RExecOpValidationError(f"unsupported trigger decision: {decision}")
        conditions = item.get("when")
        if not isinstance(conditions, list):
            raise RExecOpValidationError(f"trigger rule conditions required: {rule_id}")
        operation = item.get("operation") or {}
        if not isinstance(operation, Mapping):
            raise RExecOpValidationError(f"trigger rule operation must be a mapping: {rule_id}")
        operation_unknown = sorted(
            str(key) for key in operation if key not in ALLOWED_OPERATION_KEYS
        )
        if operation_unknown:
            raise RExecOpValidationError(
                f"unknown trigger rule operation fields: {', '.join(operation_unknown)}"
            )
        if decision == "plan_operation" and "intent" not in operation:
            raise RExecOpValidationError(f"plan_operation trigger requires intent: {rule_id}")
        priority = int(item.get("priority") or 1000)
        cooldown_seconds = int(item.get("cooldown_seconds") or 0)
        if priority < 0 or cooldown_seconds < 0:
            raise RExecOpValidationError(f"invalid trigger rule limits: {rule_id}")
        payload = {
            "id": rule_id,
            "priority": priority,
            "event_type": event_type,
            "when": list(conditions),
            "decision": decision,
            "operation": dict(operation),
            "cooldown_seconds": cooldown_seconds,
        }
        rules.append(
            TriggerRule(
                rule_id=rule_id,
                priority=priority,
                event_type=event_type,
                conditions=tuple(dict(condition) for condition in conditions),
                decision=decision,
                operation=dict(operation),
                cooldown_seconds=cooldown_seconds,
                digest=canonical_digest(payload),
            )
        )
    rules.sort(key=lambda rule: (rule.priority, rule.rule_id))
    return TriggerRuleSet(
        rule_set_id=rule_set_id,
        version=version,
        rules=tuple(rules),
        digest=canonical_digest(
            {
                "id": rule_set_id,
                "version": version,
                "rules": [rule.digest for rule in rules],
            }
        ),
    )


class TriggerService:
    def __init__(self, controller: OperationController | None = None) -> None:
        self.controller = controller or OperationController()
        self.root = self.controller.store.root / "triggers"

    def process_event(
        self,
        *,
        profile_path: str | Path,
        environment_path: Path | None,
        event_payload: Mapping[str, Any],
        catalog_path: Path | None = None,
        now: datetime | None = None,
        source: str = "event",
    ) -> dict[str, Any]:
        profile = load_profile(resolve_profile_path(profile_path))
        rule_set = _load_trigger_rules(profile)
        event = _event_payload(event_payload)
        requested_rule_set = str(event.get("rule_set") or "").strip()
        if requested_rule_set and requested_rule_set != rule_set.rule_set_id:
            raise RExecOpValidationError("trigger event requested unknown rule set")
        decision_time = now.astimezone(UTC).replace(microsecond=0) if now else _now_utc()
        occurred_at = _parse_timestamp(event["occurred_at"], "occurred_at")
        if occurred_at > decision_time + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS):
            raise RExecOpValidationError("trigger event timestamp is too far in the future")
        if occurred_at < decision_time - timedelta(seconds=MAX_PAST_SKEW_SECONDS):
            raise RExecOpValidationError("trigger event timestamp is too old")

        event_digest = _digest(event)
        payload_digest = _digest(dict(event["payload"]))
        dedupe_key = str(event.get("dedupe_key") or "").strip() or _default_dedupe_key(
            event,
            payload_digest=payload_digest,
        )
        decision_id = f"trigger-{hashlib.sha256(dedupe_key.encode()).hexdigest()[:24]}"
        directories = self._directories()
        seen_path = directories["seen"] / f"{_safe_digest(dedupe_key)}.json"
        if seen_path.exists():
            admission = _admit_trigger_decision(
                decision_id=decision_id,
                decision="drop_duplicate",
                event=event,
                event_digest=event_digest,
                rule_set=rule_set,
            )
            return self._persist_decision(
                decision_id=decision_id,
                decision="drop_duplicate",
                event=event,
                event_digest=event_digest,
                payload_digest=payload_digest,
                dedupe_key=dedupe_key,
                rule_set=rule_set,
                reason="dedupe_key_already_seen",
                operation_id=None,
                source=source,
                decision_time=decision_time,
                admission=admission,
            )

        matched = self._match_rule(rule_set, event)
        if matched is None:
            admission = _admit_trigger_decision(
                decision_id=decision_id,
                decision="ignore",
                event=event,
                event_digest=event_digest,
                rule_set=rule_set,
            )
            result = self._persist_decision(
                decision_id=decision_id,
                decision="ignore",
                event=event,
                event_digest=event_digest,
                payload_digest=payload_digest,
                dedupe_key=dedupe_key,
                rule_set=rule_set,
                reason="no_matching_trigger_rule",
                operation_id=None,
                source=source,
                decision_time=decision_time,
                admission=admission,
            )
            self._mark_seen(seen_path, result)
            return result

        cooldown_key = str(event.get("cooldown_key") or "").strip() or (
            f"{matched.rule_id}:{event['subject']}"
        )
        cooldown_path = directories["cooldowns"] / f"{_safe_digest(cooldown_key)}.json"
        if matched.cooldown_seconds and cooldown_path.exists():
            cooldown = _read_json(cooldown_path)
            last_at = _parse_timestamp(cooldown.get("last_accepted_at"), "last_accepted_at")
            until = last_at + timedelta(seconds=matched.cooldown_seconds)
            if decision_time < until:
                admission = _admit_trigger_decision(
                    decision_id=decision_id,
                    decision="cooldown_blocked",
                    event=event,
                    event_digest=event_digest,
                    rule_set=rule_set,
                    rule=matched,
                )
                result = self._persist_decision(
                    decision_id=decision_id,
                    decision="cooldown_blocked",
                    event=event,
                    event_digest=event_digest,
                    payload_digest=payload_digest,
                    dedupe_key=dedupe_key,
                    rule_set=rule_set,
                    reason="cooldown_active",
                    operation_id=None,
                    source=source,
                    decision_time=decision_time,
                    rule=matched,
                    cooldown_key=cooldown_key,
                    admission=admission,
                )
                self._mark_seen(seen_path, result)
                return result

        operation_id: str | None = None
        admission = _admit_trigger_decision(
            decision_id=decision_id,
            decision=matched.decision,
            event=event,
            event_digest=event_digest,
            rule_set=rule_set,
            rule=matched,
        )
        if matched.decision == "plan_operation":
            if admission["admission"]["allowed"] is not True:
                raise RExecOpValidationError("trigger planning admission denied")
            operation_id = self._plan_operation(
                profile=profile,
                environment_path=environment_path,
                catalog_path=catalog_path,
                rule=matched,
                source=source,
                decision_id=decision_id,
                event=event,
                event_digest=event_digest,
                payload_digest=payload_digest,
                dedupe_key=dedupe_key,
            )
        result = self._persist_decision(
            decision_id=decision_id,
            decision=matched.decision,
            event=event,
            event_digest=event_digest,
            payload_digest=payload_digest,
            dedupe_key=dedupe_key,
            rule_set=rule_set,
            reason=f"matched:{matched.rule_id}",
            operation_id=operation_id,
            source=source,
            decision_time=decision_time,
            rule=matched,
            cooldown_key=cooldown_key,
            admission=admission,
        )
        self._mark_seen(seen_path, result)
        if matched.cooldown_seconds and matched.decision == "plan_operation":
            _write_json(
                cooldown_path,
                {
                    "cooldown_key": cooldown_key,
                    "last_accepted_at": decision_time.isoformat(),
                    "decision_id": decision_id,
                    "rule_id": matched.rule_id,
                },
            )
        return result

    def _match_rule(
        self,
        rule_set: TriggerRuleSet,
        event: Mapping[str, Any],
    ) -> TriggerRule | None:
        event_type = str(event.get("type") or "")
        for rule in rule_set.rules:
            if rule.event_type != event_type:
                continue
            if all(_condition_matches(condition, event) for condition in rule.conditions):
                return rule
        return None

    def _plan_operation(
        self,
        *,
        profile: LoadedProfile,
        environment_path: Path | None,
        catalog_path: Path | None,
        rule: TriggerRule,
        source: str,
        decision_id: str,
        event: Mapping[str, Any],
        event_digest: str,
        payload_digest: str,
        dedupe_key: str,
    ) -> str:
        operation = rule.operation
        mode = str(operation.get("mode") or "dry_run")
        auto_react = (
            str(operation["auto_react"]).strip()
            if operation.get("auto_react") is not None
            else None
        )
        if catalog_path is not None or operation.get("catalog_target") or operation.get(
            "catalog_target_from"
        ):
            catalog_target = _operation_ref(
                operation,
                event,
                literal_key="catalog_target",
                path_key="catalog_target_from",
            )
            if not catalog_target:
                raise RExecOpValidationError("trigger catalog operation requires catalog_target")
            child = self.controller.plan(
                profile_path=None,
                environment_path=None,
                catalog_path=catalog_path,
                intent=str(operation["intent"]),
                target=catalog_target,
                mode=mode,
                requested_by=f"trigger:{source}:{decision_id}",
                auto_react=auto_react,
            )
        else:
            target = _operation_ref(
                operation,
                event,
                literal_key="target",
                path_key="target_from",
            )
            if environment_path is None or not target:
                raise RExecOpValidationError("trigger operation requires environment and target")
            child = self.controller.plan(
                profile_path=profile.root,
                environment_path=environment_path,
                intent=str(operation["intent"]),
                target=target,
                mode=mode,
                requested_by=f"trigger:{source}:{decision_id}",
                auto_react=auto_react,
            )
        child.metadata["trigger_decision"] = {
            "decision_id": decision_id,
            "rule_id": rule.rule_id,
            "rule_digest": rule.digest,
            "event_digest": event_digest,
            "payload_digest": payload_digest,
            "dedupe_key": dedupe_key,
        }
        event_id = self.controller.evidence.emit(
            operation_id=child.id,
            event_type=EvidenceEventType.OPERATION_TRIGGERED,
            correlation_id=child.correlation_id,
            state_before=child.state,
            state_after=child.state,
            payload=child.metadata["trigger_decision"],
        )
        child.evidence_event_ids.append(event_id)
        self.controller.store.save_operation(child)
        return child.id

    def _directories(self) -> dict[str, Path]:
        result = {
            "decisions": self.root / "decisions",
            "seen": self.root / "seen",
            "cooldowns": self.root / "cooldowns",
        }
        for path in result.values():
            secure_directory(path)
        return result

    def _persist_decision(
        self,
        *,
        decision_id: str,
        decision: str,
        event: Mapping[str, Any],
        event_digest: str,
        payload_digest: str,
        dedupe_key: str,
        rule_set: TriggerRuleSet,
        reason: str,
        operation_id: str | None,
        source: str,
        decision_time: datetime,
        rule: TriggerRule | None = None,
        cooldown_key: str | None = None,
        admission: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "artifact_type": "trigger_decision",
            "schema_version": "v0.1",
            "decision_id": decision_id,
            "decision": decision,
            "reason": reason,
            "decided_at": decision_time.isoformat(),
            "source": source,
            "event": {
                "id": event["id"],
                "source": event["source"],
                "type": event["type"],
                "subject": event["subject"],
                "occurred_at": event["occurred_at"],
                "digest": event_digest,
                "payload_digest": payload_digest,
                "dedupe_key": dedupe_key,
                "cooldown_key": cooldown_key,
            },
            "rule_set": {
                "id": rule_set.rule_set_id,
                "version": rule_set.version,
                "digest": rule_set.digest,
            },
            "operation_id": operation_id,
        }
        if rule is not None:
            result["rule"] = {"id": rule.rule_id, "digest": rule.digest}
        if admission is not None:
            result["admission"] = dict(admission)
        _write_json(self.root / "decisions" / f"{decision_id}.json", result)
        return result

    def _mark_seen(self, path: Path, decision: Mapping[str, Any]) -> None:
        _write_json(
            path,
            {
                "decision_id": decision["decision_id"],
                "decision": decision["decision"],
                "event": decision["event"],
            },
        )


def _default_dedupe_key(event: Mapping[str, Any], *, payload_digest: str) -> str:
    return ":".join(
        [
            str(event["source"]),
            str(event["id"]),
            str(event["type"]),
            str(event["subject"]),
            payload_digest,
        ]
    )


def _admit_trigger_decision(
    *,
    decision_id: str,
    decision: str,
    event: Mapping[str, Any],
    event_digest: str,
    rule_set: TriggerRuleSet,
    rule: TriggerRule | None = None,
) -> dict[str, Any]:
    operation = rule.operation if rule is not None else {}
    request = TriggerPlanningRequest(
        request_id=decision_id,
        event_ref=_sha256_ref(event_digest),
        event_type=str(event["type"]),
        decision=decision,
        rule_set_id=rule_set.rule_set_id,
        rule_set_version=rule_set.version,
        rule_set_digest=_sha256_ref(rule_set.digest),
        rule_id=rule.rule_id if rule is not None else "",
        rule_digest=_sha256_ref(rule.digest) if rule is not None else "",
        operation_intent=str(operation.get("intent") or "") if decision == "plan_operation" else "",
        operation_mode=(
            str(operation.get("mode") or "dry_run") if decision == "plan_operation" else ""
        ),
    )
    try:
        admission = admit_trigger_planning(request)
    except GovApiError as exc:
        raise RExecOpValidationError(exc.reason_code) from exc
    return {
        "request": request.as_dict(),
        "request_digest": trigger_planning_request_digest(request),
        "admission": admission.as_dict(),
        "admission_digest": trigger_planning_admission_digest(admission),
    }


def _operation_ref(
    operation: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    literal_key: str,
    path_key: str,
) -> str:
    literal = str(operation.get(literal_key) or "").strip()
    path = str(operation.get(path_key) or "").strip()
    if literal and path:
        raise RExecOpValidationError(
            f"trigger operation cannot set both {literal_key} and {path_key}"
        )
    if literal:
        return literal
    if not path:
        return ""
    value = _resolve_path(event, path)
    if value is _MISSING or not isinstance(value, str) or not value.strip():
        raise RExecOpValidationError(f"trigger operation path did not resolve: {path_key}")
    return value.strip()


def _sha256_ref(value: str) -> str:
    text = str(value or "").strip()
    return text if text.startswith("sha256:") else f"sha256:{text}"


def _safe_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RExecOpValidationError(f"invalid trigger state file: {path}") from exc
    if not isinstance(value, dict):
        raise RExecOpValidationError(f"trigger state file must be a mapping: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_write_text(path, json.dumps(dict(value), indent=2, sort_keys=True) + "\n")
