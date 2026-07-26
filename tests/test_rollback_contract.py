from __future__ import annotations

import json
import shutil
from collections.abc import Generator
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import yaml

from rexecop.adapters.govengine_port.contracts import (
    GovEngineDecision,
    GovEngineDecisionType,
    GovEngineRequest,
)
from rexecop.catalog.digest import canonical_digest
from rexecop.connectors.static_fixture import StaticFixtureRuntime
from rexecop.errors import RExecOpValidationError
from rexecop.execution.backend import StepExecutionResult
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.runtime_ops.rollback import rollback_failure_authority_digest
from rexecop.storage.file_store import FileStore
from runtime_governance_support import (
    TestAttemptGovernanceAuthority,
    governance_runtime_kwargs,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


class _RecordingGovEngineAdapter:
    def __init__(self, *decisions: GovEngineDecisionType) -> None:
        self.decisions = list(decisions)
        self.requests: list[GovEngineRequest] = []

    def evaluate(self, request: GovEngineRequest) -> GovEngineDecision:
        self.requests.append(request)
        decision = self.decisions.pop(0)
        return GovEngineDecision(
            decision_type=decision,
            summary=f"test decision: {decision.value}",
            details={"operation_id": request.operation_id},
        )


class _RecordingAttemptAuthority(TestAttemptGovernanceAuthority):
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def authorize_attempt(self, facts):  # type: ignore[no-untyped-def]
        self.requests.append(facts)
        return super().authorize_attempt(facts)


@pytest.fixture(autouse=True)
def _clear_mock_failures() -> Generator[None, None, None]:
    StaticFixtureRuntime.clear_failures()
    yield
    StaticFixtureRuntime.clear_failures()


def _controller(
    tmp_path: Path,
    adapter: _RecordingGovEngineAdapter,
    *,
    signed_attempts: bool = True,
) -> tuple[OperationController, _RecordingAttemptAuthority | None]:
    kwargs: dict[str, Any] = {}
    authority = None
    if signed_attempts:
        authority = _RecordingAttemptAuthority()
        kwargs = governance_runtime_kwargs()
        kwargs["attempt_governance_authority"] = authority
    return (
        OperationController(
            store=FileStore(tmp_path / ".rexecop"),
            govengine_adapter=adapter,
            **kwargs,
        ),
        authority,
    )


def _plan_failed_parent(
    controller: OperationController,
    *,
    rollback_mode: str = "recovery",
) -> str:
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    plan = controller.store.load_plan(operation.id)
    plan.retry_policy_summary = {
        "max_attempts": 0,
        "allowed_on": ["transient_connector_error"],
        "blocked_on": ["outcome_indeterminate"],
    }
    plan.workflow["rollback"] = {
        "mode": rollback_mode,
        "steps": [
            {
                "id": "rollback_change",
                "type": "connector",
                "connector": "fixture_source",
                "action": "apply_fixture_change",
                "timeout": "30s",
                "pause_safe": False,
            }
        ],
    }
    controller.store.save_plan(plan)
    StaticFixtureRuntime.set_failures(
        "fixture_source",
        "apply_fixture_change",
        count=1,
        error_class="transient_connector_error",
    )
    failed = controller.start(operation.id)
    assert failed.state == OperationState.FAILED.value
    StaticFixtureRuntime.clear_failures()
    return operation.id


def _attempt_records(controller: OperationController, operation_id: str) -> list[dict[str, Any]]:
    root = controller.store.root / "attempts" / operation_id
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(root.glob("*.json"))]


def _queue_payload(controller: OperationController) -> dict[str, Any]:
    path = controller.store.root / "queue" / "run_now.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _queue_approved_operation(controller: OperationController, operation_id: str) -> None:
    blocker = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target-2",
        mode="dry_run",
    )
    blocker.state = OperationState.RUNNING.value
    controller.store.save_operation(blocker)
    queued = controller.advance(operation_id)
    assert queued.state == OperationState.APPROVED.value
    assert queued.metadata["queue"]["status"] == "pending"
    assert controller.runtime.queue.list_pending() == [operation_id]


def _catalog_bound_fixture(tmp_path: Path) -> tuple[Path, Path]:
    profile = tmp_path / "catalog-profile"
    shutil.copytree(PROFILE.parent, profile)
    intent_path = profile / "intents" / "apply_fixture_change.yaml"
    intent_data = yaml.safe_load(intent_path.read_text(encoding="utf-8"))
    intent_data["intent"]["catalog"] = {
        "title": "Apply fixture change",
        "summary": "Apply one deterministic fixture mutation.",
        "target_kinds": ["fixture"],
        "required_capabilities": ["fixture_mutation"],
        "side_effect_class": "mutation",
        "validation_ref": "validation_rules/apply_fixture_change.yaml",
        "runbook_ref": "docs/apply-fixture-change.md",
    }
    intent_path.write_text(
        yaml.safe_dump(intent_data, sort_keys=False),
        encoding="utf-8",
    )
    environment = tmp_path / "catalog-environment.yaml"
    shutil.copyfile(ENVIRONMENT, environment)
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        yaml.safe_dump(
            {
                "target_catalog": {
                    "version": "0.1",
                    "targets": [
                        {
                            "id": "catalog-fixture-target",
                            "target_kind": "fixture",
                            "profile_ref": "./catalog-profile",
                            "environment_ref": "./catalog-environment.yaml",
                            "environment_target": "fixture-target",
                            "capabilities": ["fixture_mutation"],
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
    return catalog, environment


def _plan_catalog_bound_failed_parent(
    controller: OperationController,
    catalog: Path,
) -> str:
    operation = controller.plan(
        profile_path=None,
        environment_path=None,
        catalog_path=catalog,
        intent="apply_fixture_change",
        target="catalog-fixture-target",
        mode="apply",
    )
    plan = controller.store.load_plan(operation.id)
    failing_step = next(
        step for step in plan.planned_steps if step["id"] == "post_change_checkpoint"
    )
    failing_step["action"] = "bounded_unregistered_parent_action"
    plan.workflow["rollback"] = {
        "mode": "recovery",
        "steps": [
            {
                "id": "rollback_change",
                "type": "connector",
                "connector": "fixture_source",
                "action": "apply_fixture_change",
            }
        ],
    }
    controller.store.save_plan(plan)
    assert controller.start(operation.id).state == OperationState.FAILED.value
    return operation.id


def _drift_catalog_environment(environment: Path) -> None:
    data = yaml.safe_load(environment.read_text(encoding="utf-8"))
    data["environment"]["description"] = "drifted after rollback authority was persisted"
    environment.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_rollback_uses_distinct_durable_authority_and_is_idempotent(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingGovEngineAdapter(
        GovEngineDecisionType.ALLOWED,
        GovEngineDecisionType.ALLOWED,
    )
    controller, authority = _controller(tmp_path, adapter)
    assert authority is not None
    calls: list[tuple[str, str]] = []
    invoke = StaticFixtureRuntime.invoke

    def record_invoke(runtime, request):  # type: ignore[no-untyped-def]
        calls.append((request.mode, request.action))
        return invoke(runtime, request)

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)
    parent_id = _plan_failed_parent(controller)

    result = controller.rollback(parent_id)

    child_id = str(result["rollback_operation_id"])
    child = controller.get_operation(child_id)
    child_plan = controller.store.load_plan(child_id)
    assert result["success"] is True
    assert child.state == OperationState.COMPLETED.value
    assert child_id != parent_id
    assert child_plan.mode == "recovery"
    assert [step["id"] for step in child_plan.planned_steps] == ["rollback_change"]
    assert child_plan.required_connectors == ["fixture_source"]
    assert controller.get_operation(parent_id).metadata["rollback"][
        "rollback_operation_id"
    ] == child_id
    assert [request.operation_id for request in adapter.requests] == [parent_id, child_id]
    assert adapter.requests[1].preview["parent_operation_id"] == parent_id
    assert adapter.requests[1].preview["rollback_mode"] == "recovery"
    assert adapter.requests[1].preview["rollback_steps"] == child_plan.planned_steps

    assert len(authority.requests) == 2
    forward_facts, rollback_facts = authority.requests
    assert forward_facts.operation_id == parent_id
    assert rollback_facts.operation_id == child_id
    assert forward_facts.attempt_id != rollback_facts.attempt_id
    assert (forward_facts.lease_id, forward_facts.lease_epoch) != (
        rollback_facts.lease_id,
        rollback_facts.lease_epoch,
    )
    forward_attempt = _attempt_records(controller, parent_id)[0]
    rollback_attempt = _attempt_records(controller, child_id)[0]
    assert forward_attempt["attempt_id"] != rollback_attempt["attempt_id"]
    assert forward_attempt["execution_permit_ref"] != rollback_attempt[
        "execution_permit_ref"
    ]
    forward_permit = controller.store.load_execution_permit(parent_id, "apply_change")
    rollback_permit = controller.store.load_execution_permit(child_id, "rollback_change")
    assert forward_permit["governance_binding_mode"] == "signed_decision"
    assert rollback_permit["governance_binding_mode"] == "signed_decision"
    assert rollback_permit["mode"] == "recovery"
    assert rollback_permit["plan_digest"] == "sha256:" + canonical_digest(
        child_plan.as_dict()
    )
    for field in ("decision_digest", "authorization_id", "nonce_digest"):
        assert forward_permit["governance_decision"][field] != rollback_permit[
            "governance_decision"
        ][field]
    claim_records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((controller.store.root / "governance_claims").glob("*.json"))
    ]
    rollback_decision_claim = next(
        record
        for record in claim_records
        if record.get("decision_digest")
        == rollback_permit["governance_decision"]["decision_digest"]
    )
    assert rollback_decision_claim["attempt_id"] == rollback_facts.attempt_id
    assert rollback_decision_claim["nonce_digest"] == "sha256:" + sha256(
        f"test-nonce:{rollback_facts.attempt_id}".encode()
    ).hexdigest()
    forward_decision_claim = next(
        record
        for record in claim_records
        if record.get("decision_digest")
        == forward_permit["governance_decision"]["decision_digest"]
    )
    assert forward_decision_claim["nonce_digest"] != rollback_decision_claim[
        "nonce_digest"
    ]
    rollback_result = child.metadata["step_results"]["rollback_change"]
    assert rollback_result["receipt_conformance"]["conformant"] is True
    assert rollback_result["runtime_receipt_binding"]["attempt_id"] == rollback_facts.attempt_id
    child_receipt = child.metadata["shared_state"]["execution_receipt"]
    assert child_receipt["executed_steps"] == ["rollback_change"]
    assert len(child_receipt["step_receipts"]) == 1
    assert calls == [
        ("apply", "apply_fixture_change"),
        ("recovery", "apply_fixture_change"),
    ]

    replay = controller.rollback(parent_id)
    assert replay["rollback_operation_id"] == child_id
    assert replay["success"] is True
    assert len(adapter.requests) == 2
    assert len(authority.requests) == 2
    assert len(calls) == 2


def test_pending_rollback_has_independent_approval_and_stale_parent_blocks_io(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingGovEngineAdapter(
        GovEngineDecisionType.APPROVAL_REQUIRED,
        GovEngineDecisionType.APPROVAL_REQUIRED,
    )
    controller, _ = _controller(tmp_path, adapter)
    parent = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.approve(parent.id, approved_by="parent-approver")
    plan = controller.store.load_plan(parent.id)
    plan.retry_policy_summary = {
        "max_attempts": 0,
        "allowed_on": ["transient_connector_error"],
    }
    plan.workflow["rollback"] = {
        "mode": "recovery",
        "steps": [
            {
                "id": "rollback_change",
                "type": "connector",
                "connector": "fixture_source",
                "action": "apply_fixture_change",
            }
        ],
    }
    controller.store.save_plan(plan)
    StaticFixtureRuntime.set_failures(
        "fixture_source",
        "apply_fixture_change",
        count=1,
        error_class="transient_connector_error",
    )
    assert controller.start(parent.id).state == OperationState.FAILED.value
    StaticFixtureRuntime.clear_failures()

    pending = controller.rollback(parent.id)
    child_id = str(pending["rollback_operation_id"])
    assert pending["state"] == OperationState.WAITING_FOR_APPROVAL.value
    assert pending["requires_approval"] is True
    assert controller.store.load_approval(parent.id)["approved_by"] == "parent-approver"
    with pytest.raises(RExecOpValidationError, match="approval not found"):
        controller.store.load_approval(child_id)

    # A separate forward retry changes the failure authority after the rollback
    # child was persisted. Approval of the child cannot revive stale authority.
    assert controller.retry(parent.id).state == OperationState.COMPLETED.value
    controller.approve(child_id, approved_by="rollback-approver")
    invokes: list[str] = []
    invoke = StaticFixtureRuntime.invoke

    def record_invoke(runtime, request):  # type: ignore[no-untyped-def]
        invokes.append(request.mode)
        return invoke(runtime, request)

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)
    with pytest.raises(RExecOpValidationError, match="parent is no longer failed"):
        controller.start(child_id)
    assert invokes == []
    assert controller.get_operation(child_id).state == OperationState.APPROVED.value


def test_step_id_collision_never_inherits_parent_runtime_results(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingGovEngineAdapter(
        GovEngineDecisionType.ALLOWED,
        GovEngineDecisionType.APPROVAL_REQUIRED,
    )
    controller, _ = _controller(tmp_path, adapter)
    parent = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    parent_plan = controller.store.load_plan(parent.id)
    failing_step = next(
        step
        for step in parent_plan.planned_steps
        if step["id"] == "post_change_checkpoint"
    )
    failing_step["action"] = "bounded_unregistered_parent_action"
    parent_plan.workflow["rollback"] = {
        "mode": "recovery",
        "steps": [
            {
                # Deliberately collides with the successful parent connector.
                "id": "apply_change",
                "type": "connector",
                "connector": "fixture_source",
                "action": "apply_fixture_change",
            }
        ],
    }
    controller.store.save_plan(parent_plan)

    failed = controller.start(parent.id)
    assert failed.state == OperationState.FAILED.value
    parent_state = failed.metadata["shared_state"]
    assert "apply_change" in parent_state["connector_results"]
    assert "apply_change" in parent_state["mutation_states"]
    assert "pre_change_checkpoint" in parent_state["internal_results"]
    parent_state["continued_failures"] = {
        "apply_change": {"error": "parent-only", "error_class": "parent-only"}
    }
    failed.metadata["shared_state"] = parent_state
    controller.store.save_operation(failed)
    failed = controller.get_operation(parent.id)
    expected_failure_authority = rollback_failure_authority_digest(
        failed,
        parent_plan,
    )

    pending = controller.rollback(parent.id)
    child_id = str(pending["rollback_operation_id"])
    controller.approve(child_id, approved_by="rollback-approver")
    child = controller.get_operation(child_id)
    child_plan = controller.store.load_plan(child_id)
    assert child_plan.planned_steps[0]["id"] == "apply_change"
    assert child.metadata["shared_state"] == {}
    assert child.metadata["derived_operation"]["failure_authority_digest"] == (
        expected_failure_authority
    )
    pre_emission = controller.sclite_emitter.emit_operation_bundle(
        operation=child,
        plan=child_plan,
        bundle_dir=str(tmp_path / "pre-io-sclite"),
        evidence_events=controller.store.list_evidence_events(child_id),
    )
    pre_execution = pre_emission.artifacts["execution_receipt"]["execution"]
    assert pre_execution["executed_command_count"] == 0
    assert pre_execution["network_execution_performed"] is False
    for namespace in (
        "connector_results",
        "mutation_states",
        "internal_results",
        "continued_failures",
        "executed_steps",
        "step_results",
        "execution_request",
        "execution_receipt",
        "typed_execution_specs",
        "typed_execution_admissions",
        "execution_controls",
    ):
        assert namespace not in child.metadata["shared_state"]

    invokes: list[str] = []
    invoke = StaticFixtureRuntime.invoke

    def record_invoke(runtime, request):  # type: ignore[no-untyped-def]
        invokes.append(request.mode)
        return invoke(runtime, request)

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)
    completed = controller.start(child_id)

    assert completed.state == OperationState.COMPLETED.value
    assert invokes == ["recovery"]
    child_state = completed.metadata["shared_state"]
    assert set(child_state["connector_results"]) == {"apply_change"}
    assert set(child_state["mutation_states"]) == {"apply_change"}
    assert child_state["executed_steps"] == ["apply_change"]
    assert set(child_state["step_results"]) == {"apply_change"}
    assert "internal_results" not in child_state
    assert "continued_failures" not in child_state
    post_receipt = json.loads(
        (
            controller.store.operation_sclite_dir(child_id)
            / "05_execution_receipt.json"
        ).read_text(encoding="utf-8")
    )
    assert post_receipt["execution"]["executed_command_count"] == 1
    assert post_receipt["execution"]["network_execution_performed"] is True


def test_capacity_exhausted_drifted_connector_rollback_advance_is_side_effect_free(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, environment = _catalog_bound_fixture(tmp_path)
    adapter = _RecordingGovEngineAdapter(
        GovEngineDecisionType.ALLOWED,
        GovEngineDecisionType.APPROVAL_REQUIRED,
    )
    controller, _ = _controller(tmp_path, adapter)
    parent_id = _plan_catalog_bound_failed_parent(controller, catalog)
    parent = controller.get_operation(parent_id)
    parent_plan = controller.store.load_plan(parent_id)

    pending = controller.rollback(parent_id)
    child_id = str(pending["rollback_operation_id"])
    child = controller.get_operation(child_id)
    child_plan = controller.store.load_plan(child_id)
    assert child.metadata["catalog_runtime"] == parent.metadata["catalog_runtime"]
    assert child.metadata["catalog_binding"] == parent.metadata["catalog_binding"]
    assert child_plan.catalog_binding == parent_plan.catalog_binding
    controller.approve(child_id, approved_by="rollback-approver")
    blocker = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target-2",
        mode="dry_run",
    )
    blocker.state = OperationState.RUNNING.value
    controller.store.save_operation(blocker)
    assert controller.runtime.count_active_operations(
        exclude_operation_id=child_id
    ) == 1
    _drift_catalog_environment(environment)
    child_before = controller.get_operation(child_id)
    shared_before = deepcopy(child_before.metadata["shared_state"])
    evidence_before = controller.store.list_evidence_events(child_id)
    queue_before = controller.runtime.queue.list_pending()
    invokes: list[str] = []
    invoke = StaticFixtureRuntime.invoke

    def record_invoke(runtime, request):  # type: ignore[no-untyped-def]
        invokes.append(request.mode)
        return invoke(runtime, request)

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)

    with pytest.raises(RExecOpValidationError, match="catalog binding drift"):
        controller.advance(child_id)

    assert invokes == []
    child = controller.get_operation(child_id)
    assert child.state == OperationState.APPROVED.value
    assert child.metadata["shared_state"] == shared_before
    assert "queue" not in child.metadata
    assert controller.runtime.queue.list_pending() == queue_before
    assert controller.store.list_evidence_events(child_id) == evidence_before
    assert not (controller.store.root / "attempts" / child_id).exists()


def test_already_queued_rollback_catalog_drift_reconciles_claim_without_admission(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, environment = _catalog_bound_fixture(tmp_path)
    adapter = _RecordingGovEngineAdapter(
        GovEngineDecisionType.ALLOWED,
        GovEngineDecisionType.APPROVAL_REQUIRED,
    )
    controller, _ = _controller(tmp_path, adapter)
    parent_id = _plan_catalog_bound_failed_parent(controller, catalog)
    pending = controller.rollback(parent_id)
    child_id = str(pending["rollback_operation_id"])
    controller.approve(child_id, approved_by="rollback-approver")
    _queue_approved_operation(controller, child_id)

    child_before = controller.get_operation(child_id)
    shared_before = deepcopy(child_before.metadata["shared_state"])
    evidence_before = controller.store.list_evidence_events(child_id)
    attempts_before = _attempt_records(controller, child_id)
    locks_before = sorted((controller.store.root / "locks").glob("*.lock"))
    _drift_catalog_environment(environment)
    admissions: list[str] = []
    invokes: list[str] = []
    invoke = StaticFixtureRuntime.invoke

    def reject_admission(operation):  # type: ignore[no-untyped-def]
        admissions.append(operation.id)
        raise AssertionError("rollback queue preflight must precede runtime admission")

    def record_invoke(runtime, request):  # type: ignore[no-untyped-def]
        invokes.append(request.mode)
        return invoke(runtime, request)

    monkeypatch.setattr(controller.runtime, "admit_for_execution", reject_admission)
    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)

    with pytest.raises(RExecOpValidationError, match="catalog binding drift"):
        controller.process_queue()

    child = controller.get_operation(child_id)
    queue_payload = _queue_payload(controller)
    assert child.state == OperationState.APPROVED.value
    assert "queue" not in child.metadata
    assert child.metadata["shared_state"] == shared_before
    assert controller.store.list_evidence_events(child_id) == evidence_before
    assert _attempt_records(controller, child_id) == attempts_before == []
    assert controller.runtime.queue.list_pending() == []
    assert queue_payload["pending"] == []
    assert queue_payload["claims"] == {}
    assert sorted((controller.store.root / "locks").glob("*.lock")) == locks_before
    assert admissions == []
    assert invokes == []


def test_already_queued_rollback_stale_parent_reconciles_claim_without_admission(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingGovEngineAdapter(
        GovEngineDecisionType.ALLOWED,
        GovEngineDecisionType.APPROVAL_REQUIRED,
    )
    controller, _ = _controller(tmp_path, adapter)
    parent_id = _plan_failed_parent(controller)
    pending = controller.rollback(parent_id)
    child_id = str(pending["rollback_operation_id"])
    controller.approve(child_id, approved_by="rollback-approver")
    _queue_approved_operation(controller, child_id)

    child_before = controller.get_operation(child_id)
    shared_before = deepcopy(child_before.metadata["shared_state"])
    evidence_before = controller.store.list_evidence_events(child_id)
    attempts_before = _attempt_records(controller, child_id)
    locks_before = sorted((controller.store.root / "locks").glob("*.lock"))
    parent = controller.get_operation(parent_id)
    parent.state = OperationState.COMPLETED.value
    controller.store.save_operation(parent)
    admissions: list[str] = []
    invokes: list[str] = []
    invoke = StaticFixtureRuntime.invoke

    def reject_admission(operation):  # type: ignore[no-untyped-def]
        admissions.append(operation.id)
        raise AssertionError("rollback queue preflight must precede runtime admission")

    def record_invoke(runtime, request):  # type: ignore[no-untyped-def]
        invokes.append(request.mode)
        return invoke(runtime, request)

    monkeypatch.setattr(controller.runtime, "admit_for_execution", reject_admission)
    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)

    with pytest.raises(RExecOpValidationError, match="parent is no longer failed"):
        controller.process_queue()

    child = controller.get_operation(child_id)
    queue_payload = _queue_payload(controller)
    assert child.state == OperationState.APPROVED.value
    assert "queue" not in child.metadata
    assert child.metadata["shared_state"] == shared_before
    assert controller.store.list_evidence_events(child_id) == evidence_before
    assert _attempt_records(controller, child_id) == attempts_before == []
    assert controller.runtime.queue.list_pending() == []
    assert queue_payload["pending"] == []
    assert queue_payload["claims"] == {}
    assert sorted((controller.store.root / "locks").glob("*.lock")) == locks_before
    assert admissions == []
    assert invokes == []


def test_catalog_drift_before_internal_rollback_advance_blocks_marker_and_evidence(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, environment = _catalog_bound_fixture(tmp_path)
    adapter = _RecordingGovEngineAdapter(
        GovEngineDecisionType.ALLOWED,
        GovEngineDecisionType.APPROVAL_REQUIRED,
    )
    controller, _ = _controller(tmp_path, adapter)
    parent_id = _plan_catalog_bound_failed_parent(controller, catalog)
    parent_plan = controller.store.load_plan(parent_id)
    parent_plan.workflow["rollback"] = {
        "mode": "recovery",
        "steps": [
            {
                "id": "rollback_marker",
                "type": "internal",
                "action": "record_rollback_marker",
                "pause_safe": True,
            }
        ],
    }
    controller.store.save_plan(parent_plan)
    pending = controller.rollback(parent_id)
    child_id = str(pending["rollback_operation_id"])
    child_plan = controller.store.load_plan(child_id)
    assert child_plan.required_connectors == []
    controller.approve(child_id, approved_by="rollback-approver")
    evidence_before = controller.store.list_evidence_events(child_id)
    _drift_catalog_environment(environment)
    invokes: list[str] = []
    invoke = StaticFixtureRuntime.invoke

    def record_invoke(runtime, request):  # type: ignore[no-untyped-def]
        invokes.append(request.mode)
        return invoke(runtime, request)

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)

    with pytest.raises(RExecOpValidationError, match="catalog binding drift"):
        controller.advance(child_id)

    child = controller.get_operation(child_id)
    assert child.state == OperationState.APPROVED.value
    assert child.metadata["shared_state"] == {}
    assert "rollback_marker" not in child.metadata["shared_state"]
    assert "internal_results" not in child.metadata["shared_state"]
    assert controller.store.list_evidence_events(child_id) == evidence_before
    assert invokes == []
    assert not (controller.store.root / "attempts" / child_id).exists()


def test_paused_rollback_resume_catalog_drift_is_side_effect_free(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, environment = _catalog_bound_fixture(tmp_path)
    adapter = _RecordingGovEngineAdapter(
        GovEngineDecisionType.ALLOWED,
        GovEngineDecisionType.APPROVAL_REQUIRED,
    )
    controller, _ = _controller(tmp_path, adapter)
    parent_id = _plan_catalog_bound_failed_parent(controller, catalog)
    parent_plan = controller.store.load_plan(parent_id)
    parent_plan.workflow["rollback"] = {
        "mode": "recovery",
        "steps": [
            {
                "id": "rollback_checkpoint",
                "type": "internal",
                "action": "record_execution_checkpoint",
                "pause_safe": True,
            },
            {
                "id": "rollback_change",
                "type": "connector",
                "connector": "fixture_source",
                "action": "apply_fixture_change",
            },
        ],
    }
    controller.store.save_plan(parent_plan)
    pending = controller.rollback(parent_id)
    child_id = str(pending["rollback_operation_id"])
    controller.approve(child_id, approved_by="rollback-approver")
    invokes: list[str] = []
    invoke = StaticFixtureRuntime.invoke

    def record_invoke(runtime, request):  # type: ignore[no-untyped-def]
        invokes.append(request.mode)
        return invoke(runtime, request)

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)
    running = controller.advance(child_id, max_steps=1)
    assert running.state == OperationState.RUNNING.value
    assert running.current_step_id == "rollback_checkpoint"
    paused = controller.pause(child_id)
    assert paused.state == OperationState.PAUSED.value
    evidence_before = controller.store.list_evidence_events(child_id)
    shared_before = deepcopy(paused.metadata["shared_state"])
    attempts_before = _attempt_records(controller, child_id)
    _drift_catalog_environment(environment)

    with pytest.raises(RExecOpValidationError, match="catalog binding drift"):
        controller.resume(child_id)

    unchanged = controller.get_operation(child_id)
    assert unchanged.state == OperationState.PAUSED.value
    assert unchanged.metadata["shared_state"] == shared_before
    assert controller.store.list_evidence_events(child_id) == evidence_before
    assert _attempt_records(controller, child_id) == attempts_before
    assert invokes == []


def test_catalog_drift_in_rollback_pre_io_window_fails_attempt_without_io(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, environment = _catalog_bound_fixture(tmp_path)
    adapter = _RecordingGovEngineAdapter(
        GovEngineDecisionType.ALLOWED,
        GovEngineDecisionType.APPROVAL_REQUIRED,
    )
    controller, authority = _controller(tmp_path, adapter)
    assert authority is not None
    parent_id = _plan_catalog_bound_failed_parent(controller, catalog)
    pending = controller.rollback(parent_id)
    child_id = str(pending["rollback_operation_id"])
    controller.approve(child_id, approved_by="rollback-approver")
    original_pre_io = controller.orchestrator._require_attempt_fresh
    drifted = False

    def drift_then_revalidate(attempt: dict[str, Any]) -> None:
        nonlocal drifted
        if not drifted and attempt["operation_id"] == child_id:
            _drift_catalog_environment(environment)
            drifted = True
        original_pre_io(attempt)

    invokes: list[str] = []
    invoke = StaticFixtureRuntime.invoke

    def record_invoke(runtime, request):  # type: ignore[no-untyped-def]
        invokes.append(request.mode)
        return invoke(runtime, request)

    monkeypatch.setattr(
        controller.orchestrator,
        "_require_attempt_fresh",
        drift_then_revalidate,
    )
    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)

    failed = controller.start(child_id)

    assert drifted is True
    assert failed.state == OperationState.FAILED.value
    assert invokes == []
    attempts = _attempt_records(controller, child_id)
    assert len(attempts) == 1
    assert attempts[0]["side_effectful"] is True
    assert attempts[0]["status"] == "failed"
    assert all(record["status"] != "started" for record in attempts)
    assert len(authority.requests) == 2
    assert authority.requests[1].operation_id == child_id


def test_rollback_fails_closed_without_signed_attempt_authority(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    adapter = _RecordingGovEngineAdapter(
        GovEngineDecisionType.ALLOWED,
        GovEngineDecisionType.ALLOWED,
    )
    controller, _ = _controller(tmp_path, adapter, signed_attempts=False)
    parent_id = _plan_failed_parent(controller)

    with pytest.raises(
        RExecOpValidationError,
        match="canonical signed governance authority",
    ):
        controller.rollback(parent_id)

    child_id = f"{parent_id}-rollback"
    assert controller.get_operation(child_id).state == OperationState.APPROVED.value
    assert controller.get_operation(parent_id).metadata["rollback"][
        "rollback_operation_id"
    ] == child_id
    assert not (controller.store.root / "attempts" / child_id).exists()


def test_rollback_pre_io_failure_never_invokes_connector(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingGovEngineAdapter(
        GovEngineDecisionType.ALLOWED,
        GovEngineDecisionType.ALLOWED,
    )
    controller, _ = _controller(tmp_path, adapter)
    parent_id = _plan_failed_parent(controller)
    invokes: list[str] = []
    invoke = StaticFixtureRuntime.invoke

    def record_invoke(runtime, request):  # type: ignore[no-untyped-def]
        invokes.append(request.mode)
        return invoke(runtime, request)

    def reject_pre_io(_attempt: dict[str, Any]) -> None:
        raise RExecOpValidationError("execution_permit_stale: bounded test")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)
    monkeypatch.setattr(controller.orchestrator, "_require_attempt_fresh", reject_pre_io)

    result = controller.rollback(parent_id)

    child_id = str(result["rollback_operation_id"])
    assert result["state"] == OperationState.FAILED.value
    assert invokes == []
    attempts = _attempt_records(controller, child_id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "failed"


def test_rollback_receipt_ambiguity_is_not_retried_or_reinvoked(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingGovEngineAdapter(
        GovEngineDecisionType.ALLOWED,
        GovEngineDecisionType.ALLOWED,
    )
    controller, _ = _controller(tmp_path, adapter)
    parent_id = _plan_failed_parent(controller)
    invokes: list[str] = []
    invoke = StaticFixtureRuntime.invoke

    def record_invoke(runtime, request):  # type: ignore[no-untyped-def]
        invokes.append(request.mode)
        return invoke(runtime, request)

    def ambiguous_receipt(
        _attempt: dict[str, Any],
        result: StepExecutionResult,
    ) -> StepExecutionResult:
        return replace(
            result,
            success=False,
            output={**result.output, "error_class": "receipt_postcondition_failed"},
            error="runtime receipt postcondition failed: bounded ambiguity",
            receipt_conformance={
                "conformant": False,
                "reason_code": "bounded_ambiguity",
            },
        )

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_invoke)
    monkeypatch.setattr(controller.orchestrator, "_bind_attempt_receipt", ambiguous_receipt)

    result = controller.rollback(parent_id)

    child_id = str(result["rollback_operation_id"])
    assert result["state"] == OperationState.FAILED.value
    assert result["error_class"] == "outcome_indeterminate"
    assert invokes == ["recovery"]
    attempts = _attempt_records(controller, child_id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "indeterminate"
    with pytest.raises(RExecOpValidationError) as caught:
        controller.retry(child_id)
    assert caught.value.reason_code == "outcome_indeterminate"

    replay = controller.rollback(parent_id)
    assert replay["rollback_operation_id"] == child_id
    assert replay["error_class"] == "outcome_indeterminate"
    assert invokes == ["recovery"]


def test_rollback_rejected_without_workflow_block(tmp_path: Path) -> None:
    adapter = _RecordingGovEngineAdapter()
    controller, _ = _controller(tmp_path, adapter)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    controller.start(operation.id)
    with pytest.raises(RExecOpValidationError):
        controller.rollback(operation.id)
