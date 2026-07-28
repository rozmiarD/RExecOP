from __future__ import annotations

import json
import multiprocessing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from govengine.governance_decision import (
    DecisionClaimPort,
    GovernanceAuthorization,
    GovernanceDecision,
    _governance_decision_body_digest,
)
from govengine.governance_decision_signing import sign_governance_decision
from govengine.policy import RuntimeControlProjection
from govengine.signing import (
    DemoDigestSigner,
    DemoDigestVerifier,
    SigningPolicy,
    TrustPolicy,
)
from govengine.typed_execution_governance import TypedExecutionGovernanceRequest

from rexecop.adapters.govengine_port import runtime_authority as runtime_authority_module
from rexecop.adapters.govengine_port.runtime_authority import (
    RuntimeAttemptGovernanceFacts,
    SignedGovernanceDecisionBundle,
    SignedGovernedAttemptBundle,
    TrustedGovernanceDecisionConsumer,
)
from rexecop.connectors.static_fixture import StaticFixtureRuntime
from rexecop.errors import (
    RExecOpGovernanceDecisionError,
    RExecOpValidationError,
)
from rexecop.operation.controller import OperationController
from rexecop.storage.file_store import FileStore
from runtime_governance_support import (
    TestApprovalRevocations,
    TestGovernedAttemptAuthority,
    governed_runtime_kwargs,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"
SIGNING_POLICY = SigningPolicy(
    require_signature=True,
    allowed_modes=("detached_demo_digest",),
    required_signer_ids=("decision-signer",),
)


class _WrongSignerGovernedAuthority(TestGovernedAttemptAuthority):
    def authorize_governed_attempt(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        bundle = super().authorize_governed_attempt(*args, **kwargs)
        return replace(
            bundle,
            signed_artifact=sign_governance_decision(
                bundle.decision,
                signer=DemoDigestSigner(signer_id="wrong-decision-signer"),
                payload_ref=f"artifact://tests/{bundle.decision.decision_id}",
            ),
        )


class _OppositeModeGovernedAuthority(TestGovernedAttemptAuthority):
    def __init__(self, *, revocations: TestApprovalRevocations) -> None:
        super().__init__(revocations=revocations)
        self.requested_actual_modes: list[str] = []

    def authorize_governed_attempt(
        self,
        facts: RuntimeAttemptGovernanceFacts,
        *,
        typed_execution_request: TypedExecutionGovernanceRequest,
        actual_operation_mode: str,
    ) -> SignedGovernedAttemptBundle:
        self.requested_actual_modes.append(actual_operation_mode)
        opposite_mode = "recovery" if actual_operation_mode == "apply" else "apply"
        return super().authorize_governed_attempt(
            facts,
            typed_execution_request=typed_execution_request,
            actual_operation_mode=opposite_mode,
        )


class _Authority:
    def __init__(
        self,
        *,
        signer_id: str = "decision-signer",
        drift_field: str = "",
        expired: bool = False,
        max_output_bytes: int = 4096,
        output_digest_required: bool = False,
    ) -> None:
        self.signer_id = signer_id
        self.drift_field = drift_field
        self.expired = expired
        self.max_output_bytes = max_output_bytes
        self.output_digest_required = output_digest_required
        self.requests: list[RuntimeAttemptGovernanceFacts] = []

    def authorize_attempt(
        self,
        facts: RuntimeAttemptGovernanceFacts,
    ) -> SignedGovernanceDecisionBundle:
        self.requests.append(facts)
        decision = _decision(
            facts,
            expired=self.expired,
            max_output_bytes=self.max_output_bytes,
            output_digest_required=self.output_digest_required,
        )
        if self.drift_field:
            assert decision.authorization is not None
            current = getattr(decision.authorization, self.drift_field)
            if isinstance(current, int):
                replacement: object = current + 1
            elif str(current).startswith("sha256:"):
                replacement = "sha256:" + "f" * 64
            else:
                replacement = f"{current}-drift"
            grant = replace(decision.authorization, **{self.drift_field: replacement})
            decision = replace(
                decision,
                decision_digest="",
                **{"author" + "ization": grant},
            )
            decision = replace(
                decision,
                decision_digest=_governance_decision_body_digest(decision),
            )
        return SignedGovernanceDecisionBundle(
            decision=decision,
            signed_artifact=sign_governance_decision(
                decision,
                signer=DemoDigestSigner(signer_id=self.signer_id),
                payload_ref=f"artifact://governance/{decision.decision_id}",
            ),
        )


def _decision(
    facts: RuntimeAttemptGovernanceFacts,
    *,
    expired: bool = False,
    max_output_bytes: int = 4096,
    output_digest_required: bool = False,
) -> GovernanceDecision:
    now = datetime.now(UTC).replace(microsecond=0)
    digest = "sha256:" + "a" * 64
    grant = GovernanceAuthorization(
        authorization_id=f"auth:{facts.attempt_id}",
        operation_id=facts.operation_id,
        step_id=facts.step_id,
        attempt_id=facts.attempt_id,
        runtime_instance_id=facts.runtime_instance_id,
        lease_id=facts.lease_id,
        lease_epoch=facts.lease_epoch,
        fencing_token_digest=facts.fencing_token_digest,
        execution_spec_digest=facts.execution_spec_digest,
        payload_digest=facts.payload_digest,
        requested_scope_digest=facts.requested_scope_digest,
        capability_inventory_digest=facts.capability_inventory_digest,
        inventory_epoch=facts.inventory_epoch,
        policy_pack_digest="sha256:" + "b" * 64,
        policy_epoch=7,
        issued_at=(now - timedelta(seconds=40) if expired else now).isoformat(),
        expires_at=(
            now - timedelta(seconds=10) if expired else now + timedelta(seconds=30)
        ).isoformat(),
        nonce=f"nonce:{facts.attempt_id}",
    )
    decision = GovernanceDecision(
        decision_id=f"decision:{facts.attempt_id}",
        transaction_id=f"transaction:{facts.attempt_id}",
        request_digest=digest,
        status="allowed",
        reason_code="all_governance_gates_passed",
        policy_evaluation_digest=digest,
        policy_verdict_digest=digest,
        enforcement_plan_digest=digest,
        governance_trace_digest=digest,
        scope_decision_digest=digest,
        capability_compatibility_digest=digest,
        approval_attestation_digest="",
        controls=RuntimeControlProjection(
            max_output_bytes=max_output_bytes,
            output_digest_required=output_digest_required,
        ),
        **{"author" + "ization": grant},
    )
    return replace(decision, decision_digest=_governance_decision_body_digest(decision))


def _claim_in_process(root: str, decision_digest: str, nonce: str, queue: object) -> None:
    claimed = FileStore(Path(root)).claim_governance_decision_once(
        decision_digest=decision_digest,
        nonce=nonce,
        attempt_id="attempt-concurrent",
        runtime_instance_id="runtime-concurrent",
    )
    queue.put(claimed)  # type: ignore[attr-defined]


def test_signed_decision_is_bound_claimed_and_projected_to_runtime_permit(
    tmp_path: Path,
) -> None:
    authority = _Authority()
    controller = OperationController(
        FileStore(tmp_path / ".rexecop"),
        attempt_governance_authority=authority,
        governance_decision_verifier=DemoDigestVerifier(allowed_signer_ids=("decision-signer",)),
        governance_signing_policy=SIGNING_POLICY,
        governance_trust_policy=TrustPolicy(),
        capability_inventory_epoch=11,
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )

    completed = controller.start(operation.id)

    assert completed.state == "completed"
    facts = authority.requests[0]
    attempt_path = next((controller.store.root / "attempts" / operation.id).glob("*.json"))
    attempt = controller.store.load_execution_permit(operation.id, "inspect_state")
    assert facts.attempt_id in attempt_path.name
    assert attempt["attempt_id"] == facts.attempt_id
    assert attempt["governance_binding_mode"] == "signed_decision"
    assert attempt["governance_decision"]["inventory_epoch"] == 11
    assert attempt["governance_decision"]["decision_digest"].startswith("sha256:")
    assert len(list((controller.store.root / "governance_claims").glob("*.json"))) == 2
    runtime_receipt = completed.metadata["shared_state"]["execution_receipt"]
    governance = runtime_receipt["governance_bindings"]["inspect_state"]
    assert governance["runtime_receipt_binding"]["attempt_id"] == facts.attempt_id
    assert (
        governance["runtime_receipt_binding"]["runtime_permit_digest"] == attempt["permit_digest"]
    )
    assert governance["receipt_conformance"]["conformant"] is True
    assert governance["receipt_conformance"]["reason_code"] == "receipt_conforms"

    exported = controller.export_receipt(operation.id)
    bundle = Path(str(exported["bundle_dir"]))
    sclite_receipt = json.loads((bundle / "05_execution_receipt.json").read_text())
    projected = sclite_receipt["rexecop_runtime_binding"]["governance_bindings"]
    assert projected["inspect_state"]["receipt_conformance"]["conformant"] is True


def test_governance_output_limit_is_a_postcondition_failure(tmp_path: Path) -> None:
    controller = OperationController(
        FileStore(tmp_path / ".rexecop"),
        attempt_governance_authority=_Authority(max_output_bytes=1),
        governance_decision_verifier=DemoDigestVerifier(allowed_signer_ids=("decision-signer",)),
        governance_signing_policy=SIGNING_POLICY,
        governance_trust_policy=TrustPolicy(),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )

    failed = controller.start(operation.id)

    assert failed.state == "failed"
    receipt = failed.metadata["shared_state"]["execution_receipt"]
    governance = receipt["governance_bindings"]["inspect_state"]
    assert governance["receipt_conformance"]["conformant"] is False
    assert "receipt_output_limit_exceeded" in governance["receipt_conformance"]["failures"]


def test_consumer_rejects_reuse_and_untrusted_signer(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    facts = RuntimeAttemptGovernanceFacts(
        operation_id="op-1",
        step_id="step-1",
        attempt_id="attempt-1",
        runtime_instance_id="runtime-1",
        lease_id="lease-1",
        lease_epoch=1,
        fencing_token_digest="sha256:" + "1" * 64,
        execution_spec_digest="sha256:" + "2" * 64,
        payload_digest="sha256:" + "3" * 64,
        requested_scope_digest="sha256:" + "4" * 64,
        capability_inventory_digest="sha256:" + "5" * 64,
        inventory_epoch=1,
    )
    consumer = TrustedGovernanceDecisionConsumer(
        store=store,
        authority=_Authority(),
        verifier=DemoDigestVerifier(allowed_signer_ids=("decision-signer",)),
        signing_policy=SIGNING_POLICY,
        trust_policy=TrustPolicy(),
    )
    consumer.authorize_and_claim(facts)
    with pytest.raises(
        RExecOpGovernanceDecisionError,
        match="governance_decision_reused",
    ) as reused:
        consumer.authorize_and_claim(facts)
    assert reused.value.reason_code == "governance_decision_reused"
    assert reused.value.context == ()

    untrusted = TrustedGovernanceDecisionConsumer(
        store=FileStore(tmp_path / "other"),
        authority=_Authority(signer_id="other-signer"),
        verifier=DemoDigestVerifier(allowed_signer_ids=("other-signer",)),
        signing_policy=SIGNING_POLICY,
        trust_policy=TrustPolicy(),
    )
    with pytest.raises(
        RExecOpGovernanceDecisionError,
        match="governance_decision_untrusted",
    ) as untrusted_error:
        untrusted.authorize_and_claim(facts)
    assert untrusted_error.value.reason_code == "governance_decision_untrusted"
    assert untrusted_error.value.context == ("governance_decision_signer_not_allowed",)


@pytest.mark.parametrize(
    "field",
    (
        "attempt_id",
        "lease_id",
        "lease_epoch",
        "fencing_token_digest",
        "requested_scope_digest",
        "capability_inventory_digest",
        "inventory_epoch",
    ),
)
def test_trusted_decision_with_runtime_binding_drift_is_rejected(
    tmp_path: Path,
    field: str,
) -> None:
    facts = RuntimeAttemptGovernanceFacts(
        operation_id="op-drift",
        step_id="step-drift",
        attempt_id="attempt-drift",
        runtime_instance_id="runtime-drift",
        lease_id="lease-drift",
        lease_epoch=2,
        fencing_token_digest="sha256:" + "1" * 64,
        execution_spec_digest="sha256:" + "2" * 64,
        payload_digest="sha256:" + "3" * 64,
        requested_scope_digest="sha256:" + "4" * 64,
        capability_inventory_digest="sha256:" + "5" * 64,
        inventory_epoch=3,
    )
    consumer = TrustedGovernanceDecisionConsumer(
        store=FileStore(tmp_path / field),
        authority=_Authority(drift_field=field),
        verifier=DemoDigestVerifier(allowed_signer_ids=("decision-signer",)),
        signing_policy=SIGNING_POLICY,
        trust_policy=TrustPolicy(),
    )

    with pytest.raises(
        RExecOpGovernanceDecisionError,
        match="governance_decision_binding_drift",
    ) as drift:
        consumer.authorize_and_claim(facts)
    assert drift.value.reason_code == "governance_decision_binding_drift"
    assert drift.value.context == (field,)


def test_expired_signed_decision_is_rejected_before_claim(tmp_path: Path) -> None:
    facts = RuntimeAttemptGovernanceFacts(
        operation_id="op-expired",
        step_id="step-expired",
        attempt_id="attempt-expired",
        runtime_instance_id="runtime-expired",
        lease_id="lease-expired",
        lease_epoch=1,
        fencing_token_digest="sha256:" + "1" * 64,
        execution_spec_digest="sha256:" + "2" * 64,
        payload_digest="sha256:" + "3" * 64,
        requested_scope_digest="sha256:" + "4" * 64,
        capability_inventory_digest="sha256:" + "5" * 64,
        inventory_epoch=1,
    )
    store = FileStore(tmp_path / ".rexecop")
    consumer = TrustedGovernanceDecisionConsumer(
        store=store,
        authority=_Authority(expired=True),
        verifier=DemoDigestVerifier(allowed_signer_ids=("decision-signer",)),
        signing_policy=SIGNING_POLICY,
        trust_policy=TrustPolicy(),
    )

    with pytest.raises(
        RExecOpGovernanceDecisionError,
        match="governance_decision_expired",
    ) as expired:
        consumer.authorize_and_claim(facts)
    assert expired.value.reason_code == "governance_decision_expired"
    assert not list((store.root / "governance_claims").glob("*.json"))


def test_invalid_signed_decision_stops_before_connector_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original_invoke = StaticFixtureRuntime.invoke

    def counted_invoke(self: StaticFixtureRuntime, request: object) -> object:
        nonlocal calls
        calls += 1
        return original_invoke(self, request)  # type: ignore[arg-type]

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", counted_invoke)
    controller = OperationController(
        FileStore(tmp_path / ".rexecop"),
        attempt_governance_authority=_Authority(expired=True),
        governance_decision_verifier=DemoDigestVerifier(allowed_signer_ids=("decision-signer",)),
        governance_signing_policy=SIGNING_POLICY,
        governance_trust_policy=TrustPolicy(),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )

    failed = controller.start(operation.id)

    assert failed.state == "failed"
    assert calls == 0
    assert not (controller.store.root / "attempts" / operation.id).exists()


def test_decision_claim_is_atomic_across_processes(tmp_path: Path) -> None:
    root = tmp_path / ".rexecop"
    assert isinstance(FileStore(root), DecisionClaimPort)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_claim_in_process,
            args=(str(root), "sha256:" + "c" * 64, "shared-nonce", queue),
        )
        for _ in range(5)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    results = [queue.get(timeout=2) for _ in processes]
    assert results.count(True) == 1
    assert results.count(False) == 4
    store = FileStore(root)
    store.recover_started_attempts()
    assert not store.claim_governance_decision_once(
        decision_digest="sha256:" + "d" * 64,
        nonce="shared-nonce",
        attempt_id="attempt-other-digest",
        runtime_instance_id="runtime-concurrent",
    )
    assert not store.claim_governance_decision_once(
        decision_digest="sha256:" + "c" * 64,
        nonce="other-nonce",
        attempt_id="attempt-other-nonce",
        runtime_instance_id="runtime-concurrent",
    )


def test_mutation_without_signed_decision_fails_before_attempt(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
) -> None:
    controller = OperationController(FileStore(tmp_path / ".rexecop"))
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.approve(operation.id, approved_by="operator")

    result = controller.start(operation.id)

    assert result.state == "failed"
    attempts = controller.store.root / "attempts" / operation.id
    assert not attempts.exists() or not list(attempts.glob("*.json"))


def test_governed_mutation_claims_before_permit_attempt_and_io(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    events: list[str] = []
    claim_once = store.claim_governance_decision_once
    save_permit = store.save_execution_permit
    start_attempt = store.start_execution_attempt

    def record_claim(**binding):  # type: ignore[no-untyped-def]
        claimed = claim_once(**binding)
        events.append("claim")
        return claimed

    def record_permit(permit):  # type: ignore[no-untyped-def]
        events.append("permit")
        return save_permit(permit)

    def record_attempt(**binding):  # type: ignore[no-untyped-def]
        events.append("attempt")
        return start_attempt(**binding)

    monkeypatch.setattr(store, "claim_governance_decision_once", record_claim)
    monkeypatch.setattr(store, "save_execution_permit", record_permit)
    monkeypatch.setattr(store, "start_execution_attempt", record_attempt)
    revocations = TestApprovalRevocations()
    authority = TestGovernedAttemptAuthority(revocations=revocations)
    controller = OperationController(
        store,
        **governed_runtime_kwargs(
            revocations=revocations,
            authority=authority,
        ),
    )
    invoke = StaticFixtureRuntime.invoke

    def record_io(runtime, request):  # type: ignore[no-untyped-def]
        events.append("io")
        return invoke(runtime, request)

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", record_io)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.approve(operation.id, approved_by="operator")

    result = controller.start(operation.id)

    assert result.state == "completed"
    assert events == ["claim", "permit", "attempt", "io"]
    permit = store.load_execution_permit(operation.id, "apply_change")
    receipt = result.metadata["shared_state"]["execution_receipt"]
    binding = receipt["governance_bindings"]["apply_change"]["governed_admission_binding"]
    assert permit["governance_binding_mode"] == "governed_attempt"
    assert binding == permit["governed_admission_binding"]
    assert (
        permit["mode"]
        == operation.mode
        == authority.actual_modes[0]
        == permit["governed_admission_binding"]["actual_operation_mode"]
        == binding["actual_operation_mode"]
        == "apply"
    )
    assert revocations.lookups == 3


def test_opposite_valid_governed_actual_mode_fails_before_claim_or_io(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    events: list[str] = []
    revocations = TestApprovalRevocations()
    authority = _OppositeModeGovernedAuthority(revocations=revocations)

    def unexpected_claim(**_binding):  # type: ignore[no-untyped-def]
        events.append("claim")
        raise AssertionError("opposite actual mode must fail before claim")

    def unexpected_permit(_permit):  # type: ignore[no-untyped-def]
        events.append("permit")
        raise AssertionError("opposite actual mode must fail before permit")

    def unexpected_attempt(**_binding):  # type: ignore[no-untyped-def]
        events.append("attempt")
        raise AssertionError("opposite actual mode must fail before attempt")

    def unexpected_io(_runtime, _request):  # type: ignore[no-untyped-def]
        events.append("io")
        raise AssertionError("opposite actual mode must fail before connector I/O")

    monkeypatch.setattr(store, "claim_governance_decision_once", unexpected_claim)
    monkeypatch.setattr(store, "save_execution_permit", unexpected_permit)
    monkeypatch.setattr(store, "start_execution_attempt", unexpected_attempt)
    monkeypatch.setattr(StaticFixtureRuntime, "invoke", unexpected_io)
    controller = OperationController(
        store,
        **governed_runtime_kwargs(
            revocations=revocations,
            authority=authority,
        ),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.approve(operation.id, approved_by="operator")

    result = controller.start(operation.id)

    assert result.state == "failed"
    assert result.metadata["last_failure"]["error_class"] == (
        "governed_attempt_actual_operation_mode_drift"
    )
    assert authority.requested_actual_modes == ["apply"]
    assert authority.actual_modes == ["recovery"]
    assert events == []
    assert not list(store.governance_claims_dir.glob("*.json"))
    assert not (store.permits_dir / operation.id).exists()
    assert not (store.root / "attempts" / operation.id).exists()


@pytest.mark.parametrize(
    ("failure", "reason_code"),
    (
        ("revoked", "governed_attempt_approval_revoked"),
        (
            "lookup",
            "governed_attempt_approval_revocation_lookup_failed",
        ),
    ),
)
def test_current_approval_failure_after_claim_stops_before_io(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    reason_code: str,
) -> None:
    revocations = TestApprovalRevocations()
    authority = TestGovernedAttemptAuthority(revocations=revocations)
    controller = OperationController(
        FileStore(tmp_path / ".rexecop"),
        **governed_runtime_kwargs(
            revocations=revocations,
            authority=authority,
        ),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.approve(operation.id, approved_by="operator")
    original_pre_io = controller.orchestrator._require_attempt_fresh
    invokes = 0

    def fail_current_approval(attempt: dict[str, object]) -> None:
        if failure == "revoked":
            revocations.revoked = True
        else:
            revocations.fail_lookup = True
        original_pre_io(attempt)  # type: ignore[arg-type]

    def count_io(_runtime, _request):  # type: ignore[no-untyped-def]
        nonlocal invokes
        invokes += 1
        raise AssertionError("connector I/O must not run")

    monkeypatch.setattr(
        controller.orchestrator,
        "_require_attempt_fresh",
        fail_current_approval,
    )
    monkeypatch.setattr(StaticFixtureRuntime, "invoke", count_io)

    result = controller.start(operation.id)

    assert result.state == "failed"
    assert result.metadata["last_failure"]["error_class"] == reason_code
    assert invokes == 0
    attempts = controller.store.list_execution_attempts(operation.id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "failed"
    assert list((controller.store.root / "governance_claims").glob("*.json"))


def test_invalid_governed_decision_signature_stops_before_claim_or_io(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revocations = TestApprovalRevocations()
    authority = _WrongSignerGovernedAuthority(revocations=revocations)
    controller = OperationController(
        FileStore(tmp_path / ".rexecop"),
        **governed_runtime_kwargs(
            revocations=revocations,
            authority=authority,
        ),
    )
    invokes = 0

    def count_io(_runtime, _request):  # type: ignore[no-untyped-def]
        nonlocal invokes
        invokes += 1
        raise AssertionError("connector I/O must not run")

    monkeypatch.setattr(StaticFixtureRuntime, "invoke", count_io)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.approve(operation.id, approved_by="operator")

    result = controller.start(operation.id)

    assert result.state == "failed"
    assert result.metadata["last_failure"]["error_class"] == ("governed_attempt_untrusted")
    assert invokes == 0
    assert not list((controller.store.root / "governance_claims").glob("*.json"))
    assert not (controller.store.root / "attempts" / operation.id).exists()


def test_post_claim_permit_failure_requires_fresh_attempt_and_authority(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    events: list[str] = []
    fail_first_permit = True
    claim_once = store.claim_governance_decision_once
    save_permit = store.save_execution_permit
    start_attempt = store.start_execution_attempt

    def record_claim(**binding):  # type: ignore[no-untyped-def]
        claimed = claim_once(**binding)
        events.append("claim")
        return claimed

    def fail_then_save_permit(permit):  # type: ignore[no-untyped-def]
        nonlocal fail_first_permit
        events.append("permit")
        if fail_first_permit:
            fail_first_permit = False
            raise RExecOpValidationError("bounded post-claim permit failure")
        return save_permit(permit)

    def record_attempt(**binding):  # type: ignore[no-untyped-def]
        events.append("attempt")
        return start_attempt(**binding)

    monkeypatch.setattr(store, "claim_governance_decision_once", record_claim)
    monkeypatch.setattr(store, "save_execution_permit", fail_then_save_permit)
    monkeypatch.setattr(store, "start_execution_attempt", record_attempt)
    revocations = TestApprovalRevocations()
    authority = TestGovernedAttemptAuthority(revocations=revocations)
    controller = OperationController(
        store,
        **governed_runtime_kwargs(
            revocations=revocations,
            authority=authority,
        ),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    plan = store.load_plan(operation.id)
    plan.retry_policy_summary = {
        "max_attempts": 1,
        "allowed_on": ["validation_error"],
        "blocked_on": ["outcome_indeterminate"],
    }
    store.save_plan(plan)
    controller.approve(operation.id, approved_by="operator")

    completed = controller.start(operation.id)

    assert completed.state == "completed"
    assert len(authority.requests) == 2
    assert authority.requests[0].attempt_id != authority.requests[1].attempt_id
    assert events == [
        "claim",
        "permit",
        "claim",
        "permit",
        "attempt",
    ]
    claim_records = list((store.root / "governance_claims").glob("*.json"))
    assert len(claim_records) == 4


def test_governed_permit_tamper_with_recomputed_digest_stops_before_io(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revocations = TestApprovalRevocations()
    authority = TestGovernedAttemptAuthority(revocations=revocations)
    controller = OperationController(
        FileStore(tmp_path / ".rexecop"),
        **governed_runtime_kwargs(
            revocations=revocations,
            authority=authority,
        ),
    )
    original_pre_io = controller.orchestrator._require_attempt_fresh
    invokes = 0

    def tamper_then_validate(attempt: dict[str, object]) -> None:
        permit = attempt["_runtime_permit"]
        assert isinstance(permit, dict)
        binding = permit["governed_admission_binding"]
        assert isinstance(binding, dict)
        binding["actual_operation_mode"] = "recovery"
        permit["permit_digest"] = controller.orchestrator.permits.record_digest(permit)
        original_pre_io(attempt)  # type: ignore[arg-type]

    def count_io(_runtime, _request):  # type: ignore[no-untyped-def]
        nonlocal invokes
        invokes += 1
        raise AssertionError("connector I/O must not run")

    monkeypatch.setattr(
        controller.orchestrator,
        "_require_attempt_fresh",
        tamper_then_validate,
    )
    monkeypatch.setattr(StaticFixtureRuntime, "invoke", count_io)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.approve(operation.id, approved_by="operator")

    result = controller.start(operation.id)

    assert result.state == "failed"
    assert result.metadata["last_failure"]["error_class"] == "validation_error"
    assert invokes == 0


def test_governed_configuration_is_all_or_none(tmp_path: Path) -> None:
    revocations = TestApprovalRevocations()
    authority = TestGovernedAttemptAuthority(revocations=revocations)
    with pytest.raises(
        RExecOpValidationError,
        match="governed_attempt_configuration_incomplete",
    ):
        OperationController(
            FileStore(tmp_path / "authority-only"),
            governed_attempt_authority=authority,
            governance_decision_verifier=DemoDigestVerifier(
                allowed_signer_ids=("test-decision-signer",)
            ),
            governance_signing_policy=SigningPolicy(
                require_signature=True,
                allowed_modes=("detached_demo_digest",),
                required_signer_ids=("test-decision-signer",),
            ),
            governance_trust_policy=TrustPolicy(),
        )


def test_missing_optional_governed_surface_keeps_read_only_flow_working(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_surface() -> tuple[str, object]:
        raise AssertionError("optional governed surface must stay lazy")

    monkeypatch.setattr(
        runtime_authority_module,
        "_governed_admission_surface",
        unavailable_surface,
    )
    controller = OperationController(FileStore(tmp_path / ".rexecop"))
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )

    result = controller.start(operation.id)

    assert result.state == "completed"
