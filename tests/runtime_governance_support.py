from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

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
)


class TestAttemptGovernanceAuthority:
    """Signed deterministic authority for runtime-mechanics tests only."""

    __test__ = False

    def authorize_attempt(
        self,
        facts: RuntimeAttemptGovernanceFacts,
    ) -> SignedGovernanceDecisionBundle:
        now = datetime.now(UTC).replace(microsecond=0)
        marker = "sha256:" + "a" * 64
        authorization = GovernanceAuthorization(
            authorization_id=f"test-auth:{facts.attempt_id}",
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
            policy_epoch=1,
            issued_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=30)).isoformat(),
            nonce=f"test-nonce:{facts.attempt_id}",
        )
        decision = GovernanceDecision(
            decision_id=f"test-decision:{facts.attempt_id}",
            transaction_id=f"test-transaction:{facts.attempt_id}",
            request_digest=marker,
            status="allowed",
            reason_code="all_governance_gates_passed",
            policy_evaluation_digest=marker,
            policy_verdict_digest=marker,
            enforcement_plan_digest=marker,
            governance_trace_digest=marker,
            scope_decision_digest=marker,
            capability_compatibility_digest=marker,
            approval_attestation_digest="",
            controls=RuntimeControlProjection(max_output_bytes=4096),
            authorization=authorization,
        )
        decision = replace(
            decision,
            decision_digest=_governance_decision_body_digest(decision),
        )
        return SignedGovernanceDecisionBundle(
            decision=decision,
            signed_artifact=sign_governance_decision(
                decision,
                signer=DemoDigestSigner(signer_id="test-decision-signer"),
                payload_ref=f"artifact://tests/{decision.decision_id}",
            ),
        )


def governance_runtime_kwargs() -> dict[str, Any]:
    return {
        "attempt_governance_authority": TestAttemptGovernanceAuthority(),
        "governance_decision_verifier": DemoDigestVerifier(
            allowed_signer_ids=("test-decision-signer",)
        ),
        "governance_signing_policy": SigningPolicy(
            require_signature=True,
            allowed_modes=("detached_demo_digest",),
            required_signer_ids=("test-decision-signer",),
        ),
        "governance_trust_policy": TrustPolicy(),
    }
