from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.runtime_ops.worker import run_worker
from rexecop.storage.file_store import FileStore
from rexecop.triggers.service import TriggerService

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PROFILE = ROOT / "examples/profiles/runtime-fixture"
POLICY_ENV = ROOT / "examples/environments/runtime-fixture.policy.example.yaml"
NOW = datetime(2026, 6, 28, 12, 0, 0, tzinfo=UTC)


def _profile(tmp_path: Path) -> Path:
    root = tmp_path / "runtime_fixture"
    shutil.copytree(SOURCE_PROFILE, root)
    triggers = root / "triggers"
    triggers.mkdir()
    (triggers / "trigger_rules.yaml").write_text(
        yaml.safe_dump(
            {
                "trigger_rules": {
                    "id": "fixture.triggers",
                    "version": "0.1",
                    "rules": [
                        {
                            "id": "fixture.degraded.inspect",
                            "priority": 10,
                            "event_type": "fixture.state_observed",
                            "when": [
                                {
                                    "path": "payload.status",
                                    "operator": "equals",
                                    "value": "degraded",
                                }
                            ],
                            "decision": "plan_operation",
                            "operation": {
                                "intent": "inspect_fixture_state",
                                "target_from": "subject",
                                "mode": "dry_run",
                            },
                            "cooldown_seconds": 60,
                        },
                        {
                            "id": "fixture.noise.ignore",
                            "priority": 20,
                            "event_type": "fixture.state_observed",
                            "when": [
                                {
                                    "path": "payload.status",
                                    "operator": "equals",
                                    "value": "noise",
                                }
                            ],
                            "decision": "ignore",
                        },
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return root


def _add_catalog_metadata(profile: Path) -> None:
    runbook = profile / "docs" / "inspect-fixture.md"
    runbook.parent.mkdir()
    runbook.write_text("Fixture inspection runbook.\n", encoding="utf-8")
    intent_path = profile / "intents" / "inspect_fixture_state.yaml"
    intent = yaml.safe_load(intent_path.read_text(encoding="utf-8"))
    intent["intent"]["catalog"] = {
        "title": "Inspect fixture state",
        "summary": "Read one bounded fixture state.",
        "target_kinds": ["fixture"],
        "required_capabilities": ["fixture_readonly"],
        "side_effect_class": "none",
        "validation_ref": "validation_rules/inspect_fixture_state.yaml",
        "runbook_ref": "docs/inspect-fixture.md",
    }
    intent_path.write_text(yaml.safe_dump(intent, sort_keys=False), encoding="utf-8")


def _catalog(tmp_path: Path, *, profile: Path, environment: Path) -> Path:
    catalog = tmp_path / "targets.yaml"
    catalog.write_text(
        yaml.safe_dump(
            {
                "target_catalog": {
                    "version": "0.1",
                    "targets": [
                        {
                            "id": "fixture-node-01",
                            "target_kind": "fixture",
                            "profile_ref": str(profile),
                            "environment_ref": str(environment),
                            "environment_target": "fixture-target",
                            "capabilities": ["fixture_readonly"],
                            "connector_refs": ["fixture_source"],
                            "classification": {"criticality": "low"},
                        }
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return catalog


def _event(
    *,
    event_id: str = "evt-1",
    status: str = "degraded",
    subject: str = "fixture-target",
    occurred_at: datetime = NOW,
) -> dict:
    return {
        "id": event_id,
        "source": "fixture-source",
        "type": "fixture.state_observed",
        "subject": subject,
        "occurred_at": occurred_at.isoformat(),
        "payload": {"status": status},
        "rule_set": "fixture.triggers",
    }


def test_trigger_event_plans_operation_and_records_decision_evidence(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    store = FileStore(tmp_path / "runtime")
    controller = OperationController(store=store)

    decision = TriggerService(controller).process_event(
        profile_path=profile,
        environment_path=POLICY_ENV,
        event_payload=_event(),
        now=NOW,
        source="test",
    )

    assert decision["decision"] == "plan_operation"
    operation_id = decision["operation_id"]
    assert isinstance(operation_id, str)
    operation = store.load_operation(operation_id)
    assert operation.state == OperationState.PLANNED.value
    assert operation.intent == "inspect_fixture_state"
    assert operation.metadata["trigger_decision"]["decision_id"] == decision["decision_id"]
    assert decision["admission"]["request"]["decision"] == "plan_operation"
    assert decision["admission"]["request"]["operation_mode"] == "dry_run"
    assert decision["admission"]["admission"]["allowed"] is True
    assert decision["admission"]["request_digest"].startswith("sha256:")
    assert decision["admission"]["admission_digest"].startswith("sha256:")
    assert operation.metadata["trigger_decision"]["payload_digest"] == (
        decision["event"]["payload_digest"]
    )
    events = store.list_evidence_events(operation_id)
    assert [event["event_type"] for event in events if event["event_type"] == "operation_triggered"]


def test_trigger_event_plans_catalog_operation_from_event_subject(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    _add_catalog_metadata(profile)
    catalog = _catalog(tmp_path, profile=profile, environment=POLICY_ENV)
    rules_path = profile / "triggers" / "trigger_rules.yaml"
    rules = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    rules["trigger_rules"]["rules"][0]["operation"].pop("target_from")
    rules["trigger_rules"]["rules"][0]["operation"]["catalog_target_from"] = "subject"
    rules_path.write_text(yaml.safe_dump(rules, sort_keys=False), encoding="utf-8")
    store = FileStore(tmp_path / "runtime")
    controller = OperationController(store=store)

    decision = TriggerService(controller).process_event(
        profile_path=profile,
        environment_path=None,
        catalog_path=catalog,
        event_payload=_event(subject="fixture-node-01"),
        now=NOW,
        source="test",
    )

    assert decision["decision"] == "plan_operation"
    operation = store.load_operation(decision["operation_id"])
    assert operation.state == OperationState.PLANNED.value
    assert operation.target == "fixture-target"
    assert operation.metadata["catalog_runtime"] == {
        "catalog_path": str(catalog.resolve()),
        "target_id": "fixture-node-01",
    }
    assert operation.metadata["catalog_binding"]["target_id"] == "fixture-node-01"


def test_trigger_event_rejects_mutating_operation_before_planning(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    rules_path = profile / "triggers" / "trigger_rules.yaml"
    rules = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    rules["trigger_rules"]["rules"][0]["operation"]["mode"] = "apply"
    rules_path.write_text(yaml.safe_dump(rules, sort_keys=False), encoding="utf-8")
    store = FileStore(tmp_path / "runtime")
    controller = OperationController(store=store)

    with pytest.raises(RExecOpValidationError, match="trigger_planning_unsupported_operation_mode"):
        TriggerService(controller).process_event(
            profile_path=profile,
            environment_path=POLICY_ENV,
            event_payload=_event(),
            now=NOW,
            source="test",
        )

    assert store.list_operations() == []


def test_trigger_event_dedupes_by_event_identity_without_new_operation(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    store = FileStore(tmp_path / "runtime")
    controller = OperationController(store=store)
    service = TriggerService(controller)

    first = service.process_event(
        profile_path=profile,
        environment_path=POLICY_ENV,
        event_payload=_event(),
        now=NOW,
        source="test",
    )
    second = service.process_event(
        profile_path=profile,
        environment_path=POLICY_ENV,
        event_payload=_event(),
        now=NOW + timedelta(seconds=1),
        source="test",
    )

    assert first["decision"] == "plan_operation"
    assert second["decision"] == "drop_duplicate"
    assert second["admission"]["request"]["decision"] == "drop_duplicate"
    assert second["admission"]["admission"]["outcome"] == "record_only"
    assert [operation.id for operation in store.list_operations()] == [first["operation_id"]]


def test_trigger_event_cooldown_blocks_distinct_event_for_same_subject(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    store = FileStore(tmp_path / "runtime")
    controller = OperationController(store=store)
    service = TriggerService(controller)

    first = service.process_event(
        profile_path=profile,
        environment_path=POLICY_ENV,
        event_payload=_event(event_id="evt-1"),
        now=NOW,
        source="test",
    )
    second = service.process_event(
        profile_path=profile,
        environment_path=POLICY_ENV,
        event_payload=_event(event_id="evt-2"),
        now=NOW + timedelta(seconds=30),
        source="test",
    )

    assert first["decision"] == "plan_operation"
    assert second["decision"] == "cooldown_blocked"
    assert second["admission"]["request"]["decision"] == "cooldown_blocked"
    assert second["admission"]["admission"]["outcome"] == "record_only"
    assert second["event"]["cooldown_key"] == "fixture.degraded.inspect:fixture-target"
    assert [operation.id for operation in store.list_operations()] == [first["operation_id"]]


def test_trigger_event_rejects_unsafe_timestamp_skew(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    service = TriggerService(OperationController(store=FileStore(tmp_path / "runtime")))

    with pytest.raises(RExecOpValidationError, match="too far in the future"):
        service.process_event(
            profile_path=profile,
            environment_path=POLICY_ENV,
            event_payload=_event(occurred_at=NOW + timedelta(minutes=10)),
            now=NOW,
            source="test",
        )


def test_worker_processes_trigger_event_inbox_without_autostart(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    store = FileStore(tmp_path / "runtime")
    controller = OperationController(store=store)
    inbox = store.root / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "event-1.json").write_text(
        json.dumps(
            {
                "profile": str(profile),
                "env": str(POLICY_ENV),
                "trigger_event": _event(occurred_at=datetime.now(UTC)),
            }
            ),
        encoding="utf-8",
    )

    started = run_worker(controller, once=True, watch_inbox=True)

    assert started == []
    assert not list(inbox.glob("event-1.json"))
    operations = store.list_operations()
    assert len(operations) == 1
    assert operations[0].state == OperationState.PLANNED.value
