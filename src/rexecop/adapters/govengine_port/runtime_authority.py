from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from govengine.api import GovApiError
from govengine.governance_decision import GovernanceDecision
from govengine.governance_decision_signing import require_trusted_governance_decision
from govengine.signing import SignedArtifact, SigningPolicy, TrustPolicy, VerifierPort

from rexecop.errors import RExecOpValidationError
from rexecop.storage.port import RuntimeStore


@dataclass(frozen=True)
class RuntimeAttemptGovernanceFacts:
    """Bounded RExecOp-owned facts presented to the GovEngine authority."""

    operation_id: str
    step_id: str
    attempt_id: str
    runtime_instance_id: str
    lease_id: str
    lease_epoch: int
    fencing_token_digest: str
    execution_spec_digest: str
    payload_digest: str
    requested_scope_digest: str
    capability_inventory_digest: str
    inventory_epoch: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "step_id": self.step_id,
            "attempt_id": self.attempt_id,
            "runtime_instance_id": self.runtime_instance_id,
            "lease_id": self.lease_id,
            "lease_epoch": self.lease_epoch,
            "fencing_token_digest": self.fencing_token_digest,
            "execution_spec_digest": self.execution_spec_digest,
            "payload_digest": self.payload_digest,
            "requested_scope_digest": self.requested_scope_digest,
            "capability_inventory_digest": self.capability_inventory_digest,
            "inventory_epoch": self.inventory_epoch,
        }


@dataclass(frozen=True)
class SignedGovernanceDecisionBundle:
    decision: GovernanceDecision
    signed_artifact: SignedArtifact


class AttemptGovernanceAuthority(Protocol):
    """Host adapter that asks GovEngine to evaluate and sign one attempt."""

    def authorize_attempt(
        self,
        facts: RuntimeAttemptGovernanceFacts,
    ) -> SignedGovernanceDecisionBundle:
        ...


@dataclass(frozen=True)
class ClaimedGovernanceDecision:
    decision: GovernanceDecision
    signed_artifact: SignedArtifact
    facts: RuntimeAttemptGovernanceFacts


class TrustedGovernanceDecisionConsumer:
    """Verify one GovEngine decision and atomically consume its authority."""

    def __init__(
        self,
        *,
        store: RuntimeStore,
        authority: AttemptGovernanceAuthority,
        verifier: VerifierPort,
        signing_policy: SigningPolicy,
        trust_policy: TrustPolicy,
    ) -> None:
        self._store = store
        self._authority = authority
        self._verifier = verifier
        self._signing_policy = signing_policy
        self._trust_policy = trust_policy

    def authorize_and_claim(
        self,
        facts: RuntimeAttemptGovernanceFacts,
        *,
        now: datetime | None = None,
    ) -> ClaimedGovernanceDecision:
        bundle = self._authority.authorize_attempt(facts)
        try:
            decision = require_trusted_governance_decision(
                bundle.decision,
                bundle.signed_artifact,
                verifier=self._verifier,
                signing_policy=self._signing_policy,
                trust_policy=self._trust_policy,
            )
        except GovApiError as exc:
            raise RExecOpValidationError(
                f"governance_decision_untrusted: {exc}"
            ) from exc
        authorization = decision.authorization
        if not decision.allowed or authorization is None:
            raise RExecOpValidationError(
                f"governance_decision_denied: {decision.reason_code}"
            )
        self._require_bindings(authorization.as_dict(), facts)
        expires_at = datetime.fromisoformat(authorization.expires_at)
        checked_at = now or datetime.now(UTC)
        if checked_at >= expires_at:
            raise RExecOpValidationError("governance_decision_expired")
        claimed = self._store.claim_governance_decision_once(
            decision_digest=decision.decision_digest,
            nonce=authorization.nonce,
            attempt_id=facts.attempt_id,
            runtime_instance_id=facts.runtime_instance_id,
        )
        if not claimed:
            raise RExecOpValidationError("governance_decision_reused")
        return ClaimedGovernanceDecision(
            decision=decision,
            signed_artifact=bundle.signed_artifact,
            facts=facts,
        )

    @staticmethod
    def _require_bindings(
        authorization: dict[str, Any],
        facts: RuntimeAttemptGovernanceFacts,
    ) -> None:
        expected = facts.as_dict()
        drift = [
            key
            for key, value in expected.items()
            if not _binding_equal(authorization.get(key), value)
        ]
        if drift:
            raise RExecOpValidationError(
                "governance_decision_binding_drift: " + ",".join(sorted(drift))
            )


def _binding_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return hmac.compare_digest(actual, expected)
    return actual == expected
