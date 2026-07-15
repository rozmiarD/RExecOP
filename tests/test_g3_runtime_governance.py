from __future__ import annotations

import multiprocessing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from govengine.governance_decision import (
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

from rexecop.adapters.govengine_port.runtime_authority import (
    RuntimeAttemptGovernanceFacts,
    SignedGovernanceDecisionBundle,
    TrustedGovernanceDecisionConsumer,
)
from rexecop.connectors.static_fixture import StaticFixtureRuntime
from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"
SIGNING_POLICY = SigningPolicy(
    require_signature=True,
    allowed_modes=("detached_demo_digest",),
    required_signer_ids=("decision-signer",),
)


class _Authority:
    def __init__(
        self,
        *,
        signer_id: str = "decision-signer",
        drift_field: str = "",
        expired: bool = False,
    ) -> None:
        self.signer_id = signer_id
        self.drift_field = drift_field
        self.expired = expired
        self.requests: list[RuntimeAttemptGovernanceFacts] = []

    def authorize_attempt(
        self,
        facts: RuntimeAttemptGovernanceFacts,
    ) -> SignedGovernanceDecisionBundle:
        self.requests.append(facts)
        decision = _decision(facts, expired=self.expired)
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
            decision = replace(decision, authorization=grant, decision_digest="")
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
) -> GovernanceDecision:
    now = datetime.now(UTC).replace(microsecond=0)
    digest = "sha256:" + "a" * 64
    authorization = GovernanceAuthorization(
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
            now - timedelta(seconds=10)
            if expired
            else now + timedelta(seconds=30)
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
        controls=RuntimeControlProjection(max_output_bytes=4096),
        authorization=authorization,
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
        governance_decision_verifier=DemoDigestVerifier(
            allowed_signer_ids=("decision-signer",)
        ),
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
    with pytest.raises(RExecOpValidationError, match="governance_decision_reused"):
        consumer.authorize_and_claim(facts)

    untrusted = TrustedGovernanceDecisionConsumer(
        store=FileStore(tmp_path / "other"),
        authority=_Authority(signer_id="other-signer"),
        verifier=DemoDigestVerifier(allowed_signer_ids=("other-signer",)),
        signing_policy=SIGNING_POLICY,
        trust_policy=TrustPolicy(),
    )
    with pytest.raises(RExecOpValidationError, match="governance_decision_untrusted"):
        untrusted.authorize_and_claim(facts)


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

    with pytest.raises(RExecOpValidationError, match="governance_decision_binding_drift"):
        consumer.authorize_and_claim(facts)


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

    with pytest.raises(RExecOpValidationError, match="governance_decision_expired"):
        consumer.authorize_and_claim(facts)
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
        governance_decision_verifier=DemoDigestVerifier(
            allowed_signer_ids=("decision-signer",)
        ),
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


def test_mutation_without_signed_decision_fails_before_attempt(tmp_path: Path) -> None:
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
