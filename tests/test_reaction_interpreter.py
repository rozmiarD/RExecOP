from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from sclite import build_observation_envelope

from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.operation.model import Operation, utc_now_iso
from rexecop.operation.state import OperationState
from rexecop.profile.loader import load_profile
from rexecop.reaction.compiler import compile_reaction_pack
from rexecop.reaction.evaluator import evaluate_reaction
from rexecop.reaction.model import ReactionContext
from rexecop.reaction.service import ReactionService
from rexecop.storage.file_store import FileStore

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PROFILE = ROOT / "examples/profiles/runtime-fixture"
POLICY_ENV = ROOT / "examples/environments/runtime-fixture.policy.example.yaml"


def _pack(*, outcome: str = "run_intent", intent_ref: str | None = "inspect_fixture_state") -> dict:
    rule: dict = {
        "id": "fixture.degraded.inspect",
        "priority": 10,
        "when": [{"path": "facts.state.status", "operator": "equals", "value": "degraded"}],
        "finding": {
            "kind": "fixture.degraded",
            "severity": "medium",
            "summary": "Fixture state is degraded.",
        },
        "outcome": outcome,
    }
    if intent_ref is not None:
        rule["intent_ref"] = intent_ref
    return {
        "reaction_pack": {
            "id": "fixture-reactions",
            "version": "0.1",
            "budgets": {"max_depth": 3, "max_reactions": 5},
            "rules": [rule],
            "fallback": {
                "id": "fixture.fallback",
                "priority": 1000,
                "when": [],
                "finding": {
                    "kind": "fixture.no_match",
                    "severity": "info",
                    "summary": "No deterministic reaction matched.",
                },
                "outcome": "no_op",
            },
        }
    }


def _profile(tmp_path: Path, *, pack: dict | None = None, name: str = "runtime_fixture") -> Path:
    root = tmp_path / name
    shutil.copytree(SOURCE_PROFILE, root)
    profile_data = yaml.safe_load((root / "profile.yaml").read_text(encoding="utf-8"))
    profile_data["profile_contract"]["name"] = name
    (root / "profile.yaml").write_text(yaml.safe_dump(profile_data), encoding="utf-8")
    reactions = root / "reactions"
    reactions.mkdir()
    (reactions / "reaction_pack.yaml").write_text(
        yaml.safe_dump(pack or _pack(), sort_keys=False),
        encoding="utf-8",
    )
    return root


def _add_catalog_metadata(profile_root: Path) -> None:
    runbook = profile_root / "docs" / "inspect-fixture.md"
    runbook.parent.mkdir()
    runbook.write_text("Fixture inspection runbook.\n", encoding="utf-8")
    intent_path = profile_root / "intents" / "inspect_fixture_state.yaml"
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


def _catalog(
    path: Path,
    *,
    profile_root: Path,
    environment_path: Path,
    capabilities: list[str] | None = None,
) -> Path:
    catalog = path / "targets.yaml"
    catalog.write_text(
        yaml.safe_dump(
            {
                "target_catalog": {
                    "version": "0.1",
                    "targets": [
                        {
                            "id": "fixture-node-01",
                            "target_kind": "fixture",
                            "profile_ref": str(profile_root),
                            "environment_ref": str(environment_path),
                            "environment_target": "fixture-target",
                            "capabilities": capabilities or ["fixture_readonly"],
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


def _observation(path: Path, profile_root: Path, *, status: str) -> Path:
    value = _observation_value(profile_root, operation_id="source-op", status=status)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _observation_value(
    profile_root: Path,
    *,
    operation_id: str,
    status: str,
) -> dict:
    profile = load_profile(profile_root)
    pack = compile_reaction_pack(profile)
    return build_observation_envelope(
        observation_id=f"obs-{status}",
        observed_at="2026-06-22T20:00:00+00:00",
        profile_ref={"id": profile.name, "version": profile.version, "digest": pack.profile_digest},
        operation_id=operation_id,
        intent_id="inspect_state",
        target_id="fixture-target",
        facts={"state": {"status": status}},
    )


def _completed_source_operation(profile_root: Path, *, observation: dict) -> Operation:
    now = utc_now_iso()
    return Operation(
        id="source-op",
        profile=load_profile(profile_root).name,
        environment="runtime-fixture",
        intent="inspect_state",
        target="fixture-target",
        mode="dry_run",
        requested_by="operator",
        state=OperationState.COMPLETED.value,
        created_at=now,
        updated_at=now,
        metadata={"shared_state": {"reaction_observation": observation}},
    )


def test_evaluator_is_deterministic_and_fail_closed_on_budgets(tmp_path: Path) -> None:
    profile = load_profile(_profile(tmp_path))
    pack = compile_reaction_pack(profile)
    observation = {"facts": {"state": {"status": "degraded"}}}

    first = evaluate_reaction(pack, observation, ReactionContext())
    second = evaluate_reaction(pack, observation, ReactionContext())
    assert first == second
    assert first.rule.rule_id == "fixture.degraded.inspect"
    assert (
        evaluate_reaction(pack, observation, ReactionContext(depth=3)).reason
        == "max_reaction_depth_exceeded"
    )
    assert (
        evaluate_reaction(pack, observation, ReactionContext(reaction_count=5)).reason
        == "reaction_budget_exhausted"
    )
    assert (
        evaluate_reaction(
            pack,
            observation,
            ReactionContext(visited_rule_digests=(first.rule.digest,)),
        ).reason
        == "reaction_cycle_detected"
    )


def test_compiler_rejects_mutating_profile_intent(tmp_path: Path) -> None:
    profile_root = _profile(
        tmp_path,
        pack=_pack(outcome="run_intent", intent_ref="apply_fixture_change"),
    )
    with pytest.raises(RExecOpValidationError, match="mutating modes"):
        compile_reaction_pack(load_profile(profile_root))


def test_no_op_reaction_emits_replayable_sclite_chain(tmp_path: Path) -> None:
    profile_root = _profile(tmp_path)
    service = ReactionService(OperationController(store=FileStore(tmp_path / "runtime")))
    result = service.plan(
        profile_path=profile_root,
        environment_path=POLICY_ENV,
        observation_path=_observation(tmp_path / "healthy.json", profile_root, status="healthy"),
        target="fixture-target",
    )

    assert result["reaction_plan"]["outcome"] == "no_op"
    assert result["reaction_plan"]["child_operation_id"] is None
    assert service.replay(result["reaction_id"])["reaction_semantics"] == "passed"


def test_admitted_reaction_runs_normal_lifecycle_and_binds_receipt(tmp_path: Path) -> None:
    profile_root = _profile(tmp_path)
    environment = yaml.safe_load(POLICY_ENV.read_text(encoding="utf-8"))
    environment["environment"]["profile"] = "runtime_fixture"
    environment_path = tmp_path / "environment.yaml"
    environment_path.write_text(yaml.safe_dump(environment), encoding="utf-8")
    service = ReactionService(OperationController(store=FileStore(tmp_path / "runtime")))
    planned = service.plan(
        profile_path=profile_root,
        environment_path=environment_path,
        observation_path=_observation(tmp_path / "degraded.json", profile_root, status="degraded"),
        target="fixture-target",
    )

    plan = planned["reaction_plan"]
    assert plan["outcome"] == "run_intent"
    assert plan["admission"]["status"] == "admitted"
    assert plan["admission"]["decision"] == "allow"
    completed = service.start(planned["reaction_id"])
    assert completed["child_state"] == "completed"
    assert completed["validation"]["passed"] is True
    replay = service.replay(planned["reaction_id"])
    assert replay["checked_entries"] == [
        "observation",
        "finding",
        "reaction_plan",
        "execution_receipt",
    ]


def test_reaction_records_govengine_automation_admission_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAutomationTransitionRequest:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        @classmethod
        def from_mapping(cls, payload: dict) -> FakeAutomationTransitionRequest:
            return cls(dict(payload))

        def as_dict(self) -> dict:
            return dict(self.payload)

    class FakeAdmission:
        def as_dict(self) -> dict:
            return {
                "decision_id": "automation-transition:reaction-test",
                "subject_ref": "sha256:" + "1" * 64,
                "subject_kind": "operator_action",
                "outcome": "allowed",
                "allowed": True,
                "reason_code": "automation_transition_allowed",
                "blockers": [],
                "signal": {},
                "metadata": {},
            }

    class FakeExplanation:
        def as_dict(self) -> dict:
            return {
                "schema_version": "v0.1",
                "status": "explained",
                "reason_code": "automation_transition_allowed",
            }

    import govengine

    monkeypatch.setattr(
        govengine,
        "AutomationTransitionRequest",
        FakeAutomationTransitionRequest,
        raising=False,
    )
    monkeypatch.setattr(
        govengine,
        "admit_automation_transition",
        lambda request: FakeAdmission(),
        raising=False,
    )
    monkeypatch.setattr(
        govengine,
        "automation_transition_request_digest",
        lambda request: "sha256:" + "2" * 64,
        raising=False,
    )
    monkeypatch.setattr(
        govengine,
        "automation_transition_admission_digest",
        lambda admission: "sha256:" + "3" * 64,
        raising=False,
    )
    monkeypatch.setattr(
        govengine,
        "explain_automation_transition",
        lambda request: FakeExplanation(),
        raising=False,
    )

    profile_root = _profile(tmp_path)
    environment_path = tmp_path / "environment.yaml"
    environment_path.write_text(POLICY_ENV.read_text(encoding="utf-8"), encoding="utf-8")
    service = ReactionService(OperationController(store=FileStore(tmp_path / "runtime")))

    planned = service.plan(
        profile_path=profile_root,
        environment_path=environment_path,
        observation_path=_observation(tmp_path / "degraded.json", profile_root, status="degraded"),
        target="fixture-target",
    )
    reaction_id = planned["reaction_id"]
    binding = planned["automation_admission"]

    assert binding["status"] == "admitted"
    assert binding["admission_digest"] == "sha256:" + "3" * 64
    assert binding["automation_chain_digest"].startswith("sha256:")
    assert (service.root / reaction_id / "05_automation_chain.json").is_file()
    explanation = service.explain(reaction_id)
    assert explanation["automation_admission"]["decision_digest"] == "sha256:" + "3" * 64
    assert explanation["automation_chain"]["status"] == "passed"


def test_repeated_reaction_plan_reuses_child_operation(tmp_path: Path) -> None:
    profile_root = _profile(tmp_path)
    environment_path = tmp_path / "environment.yaml"
    environment_path.write_text(POLICY_ENV.read_text(encoding="utf-8"), encoding="utf-8")
    store = FileStore(tmp_path / "runtime")
    service = ReactionService(OperationController(store=store))
    observation_path = _observation(tmp_path / "degraded.json", profile_root, status="degraded")
    arguments = {
        "profile_path": profile_root,
        "environment_path": environment_path,
        "observation_path": observation_path,
        "target": "fixture-target",
    }

    first = service.plan(**arguments)
    second = service.plan(**arguments)

    assert second["idempotent_replay"] is True
    assert second["reaction_plan"] == first["reaction_plan"]
    assert len(store.list_operations()) == 1


def test_reaction_plan_can_use_profile_observation_from_completed_operation(
    tmp_path: Path,
) -> None:
    profile_root = _profile(tmp_path)
    environment_path = tmp_path / "environment.yaml"
    environment_path.write_text(POLICY_ENV.read_text(encoding="utf-8"), encoding="utf-8")
    store = FileStore(tmp_path / "runtime")
    observation = _observation_value(profile_root, operation_id="source-op", status="degraded")
    store.save_operation(_completed_source_operation(profile_root, observation=observation))
    service = ReactionService(OperationController(store=store))

    result = service.plan(
        profile_path=profile_root,
        environment_path=environment_path,
        source_operation_id="source-op",
        target="fixture-target",
    )

    assert result["reaction_plan"]["outcome"] == "run_intent"
    stored = store.load_operation("source-op")
    assert stored.metadata["shared_state"]["reaction_observation"] == observation


def test_auto_react_plan_only_creates_idempotent_child_plan(tmp_path: Path) -> None:
    profile_root = _profile(tmp_path)
    environment_path = tmp_path / "environment.yaml"
    environment_path.write_text(POLICY_ENV.read_text(encoding="utf-8"), encoding="utf-8")
    store = FileStore(tmp_path / "runtime")
    observation = _observation_value(profile_root, operation_id="source-op", status="degraded")
    source = _completed_source_operation(profile_root, observation=observation)
    source.metadata["profile_root"] = str(profile_root)
    source.metadata["environment_path"] = str(environment_path)
    source.metadata["auto_react"] = {
        "mode": "plan_only",
        "depth": 0,
        "reaction_count": 0,
        "visited_rule_digests": [],
    }
    store.save_operation(source)
    controller = OperationController(store=store)

    first = controller._maybe_plan_auto_reaction(source)
    second = controller._maybe_plan_auto_reaction(source)

    assert first is not None
    assert first["status"] == "planned"
    assert first["outcome"] == "run_intent"
    child_id = first["child_operation_id"]
    assert isinstance(child_id, str)
    assert child_id
    assert store.load_operation(child_id).state == OperationState.PLANNED.value
    assert second is not None
    assert second["idempotent_replay"] is True
    assert second["child_operation_id"] == child_id
    assert len(store.list_operations()) == 2


def test_auto_react_plan_only_no_op_never_creates_child_operation(tmp_path: Path) -> None:
    profile_root = _profile(tmp_path)
    environment_path = tmp_path / "environment.yaml"
    environment_path.write_text(POLICY_ENV.read_text(encoding="utf-8"), encoding="utf-8")
    store = FileStore(tmp_path / "runtime")
    observation = _observation_value(profile_root, operation_id="source-op", status="healthy")
    source = _completed_source_operation(profile_root, observation=observation)
    source.metadata["profile_root"] = str(profile_root)
    source.metadata["environment_path"] = str(environment_path)
    source.metadata["auto_react"] = {"mode": "plan_only"}
    store.save_operation(source)
    controller = OperationController(store=store)

    result = controller._maybe_plan_auto_reaction(source)

    assert result is not None
    assert result["status"] == "planned"
    assert result["outcome"] == "no_op"
    assert result["child_operation_id"] is None
    assert len(store.list_operations()) == 1


def test_reaction_child_plan_preserves_source_catalog_binding(tmp_path: Path) -> None:
    profile_root = _profile(tmp_path)
    _add_catalog_metadata(profile_root)
    environment_path = tmp_path / "environment.yaml"
    environment_path.write_text(POLICY_ENV.read_text(encoding="utf-8"), encoding="utf-8")
    catalog_path = _catalog(
        tmp_path,
        profile_root=profile_root,
        environment_path=environment_path,
    )
    store = FileStore(tmp_path / "runtime")
    controller = OperationController(store=store)
    source = controller.plan(
        profile_path=None,
        environment_path=None,
        catalog_path=catalog_path,
        intent="inspect_fixture_state",
        target="fixture-node-01",
        mode="dry_run",
    )
    observation = _observation_value(
        profile_root,
        operation_id=source.id,
        status="degraded",
    )
    source.state = OperationState.COMPLETED.value
    source.metadata["shared_state"] = {"reaction_observation": observation}
    store.save_operation(source)

    result = ReactionService(controller).plan(
        profile_path=profile_root,
        environment_path=environment_path,
        source_operation_id=source.id,
        target="fixture-target",
    )

    child_id = result["reaction_plan"]["child_operation_id"]
    assert isinstance(child_id, str)
    child = store.load_operation(child_id)
    assert child.state == OperationState.PLANNED.value
    assert child.metadata["catalog_runtime"] == {
        "catalog_path": str(catalog_path.resolve()),
        "target_id": "fixture-node-01",
    }
    child_plan = store.load_plan(child.id)
    assert child_plan.catalog_binding["target_id"] == "fixture-node-01"
    assert child_plan.catalog_binding == child.metadata["catalog_binding"]


def test_reaction_child_plan_blocks_catalog_drift_before_child_creation(
    tmp_path: Path,
) -> None:
    profile_root = _profile(tmp_path)
    _add_catalog_metadata(profile_root)
    environment_path = tmp_path / "environment.yaml"
    environment_path.write_text(POLICY_ENV.read_text(encoding="utf-8"), encoding="utf-8")
    catalog_path = _catalog(
        tmp_path,
        profile_root=profile_root,
        environment_path=environment_path,
    )
    store = FileStore(tmp_path / "runtime")
    controller = OperationController(store=store)
    source = controller.plan(
        profile_path=None,
        environment_path=None,
        catalog_path=catalog_path,
        intent="inspect_fixture_state",
        target="fixture-node-01",
        mode="dry_run",
    )
    observation = _observation_value(
        profile_root,
        operation_id=source.id,
        status="degraded",
    )
    source.state = OperationState.COMPLETED.value
    source.metadata["shared_state"] = {"reaction_observation": observation}
    store.save_operation(source)
    _catalog(
        tmp_path,
        profile_root=profile_root,
        environment_path=environment_path,
        capabilities=["different_capability"],
    )

    with pytest.raises(RExecOpValidationError, match="catalog operation is not applicable"):
        ReactionService(controller).plan(
            profile_path=profile_root,
            environment_path=environment_path,
            source_operation_id=source.id,
            target="fixture-target",
        )
    assert [operation.id for operation in store.list_operations()] == [source.id]


def test_reaction_plan_rejects_missing_or_ambiguous_observation_source(
    tmp_path: Path,
) -> None:
    profile_root = _profile(tmp_path)
    service = ReactionService(OperationController(store=FileStore(tmp_path / "runtime")))
    observation_path = _observation(
        tmp_path / "degraded.json",
        profile_root,
        status="degraded",
    )

    with pytest.raises(RExecOpValidationError, match="exactly one"):
        service.plan(
            profile_path=profile_root,
            environment_path=POLICY_ENV,
            target="fixture-target",
        )
    with pytest.raises(RExecOpValidationError, match="exactly one"):
        service.plan(
            profile_path=profile_root,
            environment_path=POLICY_ENV,
            observation_path=observation_path,
            source_operation_id="source-op",
            target="fixture-target",
        )


def test_reaction_plan_rejects_non_completed_source_operation(tmp_path: Path) -> None:
    profile_root = _profile(tmp_path)
    store = FileStore(tmp_path / "runtime")
    observation = _observation_value(profile_root, operation_id="source-op", status="degraded")
    operation = _completed_source_operation(profile_root, observation=observation)
    operation.state = OperationState.RUNNING.value
    store.save_operation(operation)
    service = ReactionService(OperationController(store=store))

    with pytest.raises(RExecOpValidationError, match="requires completed operation"):
        service.plan(
            profile_path=profile_root,
            environment_path=POLICY_ENV,
            source_operation_id="source-op",
            target="fixture-target",
        )


def test_reaction_plan_rejects_source_operation_observation_mismatch(
    tmp_path: Path,
) -> None:
    profile_root = _profile(tmp_path)
    store = FileStore(tmp_path / "runtime")
    observation = _observation_value(profile_root, operation_id="other-op", status="degraded")
    store.save_operation(_completed_source_operation(profile_root, observation=observation))
    service = ReactionService(OperationController(store=store))

    with pytest.raises(RExecOpValidationError, match="source operation mismatch"):
        service.plan(
            profile_path=profile_root,
            environment_path=POLICY_ENV,
            source_operation_id="source-op",
            target="fixture-target",
        )


def test_two_profile_snapshots_compile_without_core_domain_assumptions(tmp_path: Path) -> None:
    first = compile_reaction_pack(load_profile(_profile(tmp_path, name="fixture-a")))
    second = compile_reaction_pack(load_profile(_profile(tmp_path, name="fixture-b")))
    assert first.profile_digest != second.profile_digest
    assert first.rules[0].outcome == second.rules[0].outcome == "run_intent"


def test_reaction_fails_closed_on_unenforceable_obligations(tmp_path: Path) -> None:
    profile_root = _profile(tmp_path)
    environment = yaml.safe_load(POLICY_ENV.read_text(encoding="utf-8"))
    environment["environment"]["policy_pack"] = {
        "policy_id": "obligated-reaction",
        "version": "1",
        "rules": [
            {
                "rule_id": "allow-with-receipt-obligation",
                "effect": "allow_with_obligations",
                "conditions": {"action.mode": "read"},
                "obligations": [{"obligation_id": "receipt", "kind": "receipt"}],
            }
        ],
    }
    environment_path = tmp_path / "obligated-environment.yaml"
    environment_path.write_text(yaml.safe_dump(environment), encoding="utf-8")
    service = ReactionService(OperationController(store=FileStore(tmp_path / "runtime")))

    result = service.plan(
        profile_path=profile_root,
        environment_path=environment_path,
        observation_path=_observation(tmp_path / "degraded.json", profile_root, status="degraded"),
        target="fixture-target",
    )["reaction_plan"]

    assert result["outcome"] == "escalate"
    assert result["child_operation_id"] is None
    assert result["admission"]["status"] == "blocked"
    assert result["admission"]["decision"] == "allow_with_obligations"


def test_llm_proposal_validation_never_grants_execution(tmp_path: Path) -> None:
    profile_root = _profile(tmp_path)
    proposal = {
        "artifact_type": "escalation_proposal",
        "schema_version": "v0.1",
        "schema_ref": "schemas/escalation_proposal.v0.1.schema.json",
        "proposal_id": "proposal-1",
        "reaction_id": "reaction-1",
        "created_at": "2026-06-22T20:00:00+00:00",
        "suggested_outcome": "run_intent",
        "intent_ref": "inspect_fixture_state",
        "explanation": "Re-run the bounded read-only inspection.",
        "evidence_refs": ["01_observation.json"],
        "authority": {
            "trusted": False,
            "may_execute": False,
            "requires_profile_validation": True,
            "requires_govengine_admission": True,
        },
    }
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
    service = ReactionService(OperationController(store=FileStore(tmp_path / "runtime")))

    result = service.validate_proposal(profile_path=profile_root, proposal_path=proposal_path)

    assert result["status"] == "valid_untrusted_proposal"
    assert result["may_execute"] is False
    assert result["requires_govengine_admission"] is True
