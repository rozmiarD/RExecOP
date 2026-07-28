from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from govengine.api import GovApiError
from govengine.approvals import ApprovalRevocationPort
from govengine.governance import GovernanceRequest
from govengine.governance_decision import DecisionClaimPort, GovernanceDecision
from govengine.governance_decision_signing import require_trusted_governance_decision
from govengine.signing import SignedArtifact, SigningPolicy, TrustPolicy, VerifierPort
from govengine.typed_execution_governance import (
    TypedExecutionGovernanceRequest,
    validate_typed_execution_governance_request,
)

from rexecop.errors import RExecOpGovernanceDecisionError

if TYPE_CHECKING:
    from govengine.typed_execution_governed_admission import (
        TypedExecutionGovernedAdmission,
    )

GOVERNED_ATTEMPT_BINDING_SCHEMA = "rexecop.governed_admission_binding.v0.1"


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
    ) -> SignedGovernanceDecisionBundle: ...


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
        store: DecisionClaimPort,
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
            raise RExecOpGovernanceDecisionError(
                "governance_decision_untrusted",
                context=(exc.reason_code,),
            ) from exc
        grant = decision.authorization
        if not decision.allowed or grant is None:
            raise RExecOpGovernanceDecisionError(
                "governance_decision_denied",
                context=(decision.reason_code,),
            )
        self._require_bindings(grant.as_dict(), facts)
        expires_at = datetime.fromisoformat(grant.expires_at)
        checked_at = now or datetime.now(UTC)
        if checked_at >= expires_at:
            raise RExecOpGovernanceDecisionError("governance_decision_expired")
        claimed = self._store.claim_governance_decision_once(
            decision_digest=decision.decision_digest,
            nonce=grant.nonce,
            attempt_id=facts.attempt_id,
            runtime_instance_id=facts.runtime_instance_id,
        )
        if not claimed:
            raise RExecOpGovernanceDecisionError("governance_decision_reused")
        return ClaimedGovernanceDecision(
            decision=decision,
            signed_artifact=bundle.signed_artifact,
            facts=facts,
        )

    @staticmethod
    def _require_bindings(
        grant: dict[str, Any],
        facts: RuntimeAttemptGovernanceFacts,
    ) -> None:
        expected = facts.as_dict()
        drift = [
            key for key, value in expected.items() if not _binding_equal(grant.get(key), value)
        ]
        if drift:
            raise RExecOpGovernanceDecisionError(
                "governance_decision_binding_drift",
                context=tuple(sorted(drift)),
            )


@dataclass(frozen=True)
class SignedGovernedAttemptBundle:
    """Exact GovEngine owner records returned by one governed-attempt authority."""

    governance_request: GovernanceRequest
    governed_admission: TypedExecutionGovernedAdmission
    decision: GovernanceDecision
    signed_artifact: SignedArtifact


class GovernedAttemptAuthority(Protocol):
    """Host adapter that evaluates and signs one approval-attested attempt."""

    def authorize_governed_attempt(
        self,
        facts: RuntimeAttemptGovernanceFacts,
        *,
        typed_execution_request: TypedExecutionGovernanceRequest,
        actual_operation_mode: str,
    ) -> SignedGovernedAttemptBundle: ...


@dataclass(frozen=True)
class ClaimedGovernedAttempt:
    governance_request: GovernanceRequest
    governed_admission: TypedExecutionGovernedAdmission
    decision: GovernanceDecision
    signed_artifact: SignedArtifact
    typed_execution_request: TypedExecutionGovernanceRequest
    expected_actual_operation_mode: str
    facts: RuntimeAttemptGovernanceFacts


class TrustedGovernedAttemptConsumer:
    """Verify, cross-bind and consume one GovEngine governed attempt."""

    def __init__(
        self,
        *,
        store: DecisionClaimPort,
        authority: GovernedAttemptAuthority,
        verifier: VerifierPort,
        signing_policy: SigningPolicy,
        trust_policy: TrustPolicy,
        approval_revocation_port: ApprovalRevocationPort,
    ) -> None:
        self._store = store
        self._authority = authority
        self._verifier = verifier
        self._signing_policy = signing_policy
        self._trust_policy = trust_policy
        self._approval_revocation_port = approval_revocation_port
        _governed_admission_surface()

    def authorize_and_claim(
        self,
        facts: RuntimeAttemptGovernanceFacts,
        *,
        typed_execution_request: Mapping[str, Any] | TypedExecutionGovernanceRequest,
        actual_operation_mode: str,
        now: datetime | None = None,
    ) -> ClaimedGovernedAttempt:
        checked_typed = validate_typed_execution_governance_request(typed_execution_request)
        bundle = self._authority.authorize_governed_attempt(
            facts,
            typed_execution_request=checked_typed,
            actual_operation_mode=actual_operation_mode,
        )
        claim = ClaimedGovernedAttempt(
            governance_request=bundle.governance_request,
            governed_admission=bundle.governed_admission,
            decision=bundle.decision,
            signed_artifact=bundle.signed_artifact,
            typed_execution_request=checked_typed,
            expected_actual_operation_mode=actual_operation_mode,
            facts=facts,
        )
        self.require_current(claim, now=now)
        grant = claim.decision.authorization
        assert grant is not None
        claimed = self._store.claim_governance_decision_once(
            decision_digest=claim.decision.decision_digest,
            nonce=grant.nonce,
            attempt_id=facts.attempt_id,
            runtime_instance_id=facts.runtime_instance_id,
        )
        if not claimed:
            raise RExecOpGovernanceDecisionError("governance_decision_reused")
        return claim

    def require_current(
        self,
        claim: ClaimedGovernedAttempt,
        *,
        now: datetime | None = None,
    ) -> None:
        (
            _surface_version,
            validate_governed_admission,
        ) = _governed_admission_surface()
        checked_at = now or datetime.now(UTC)
        try:
            decision = require_trusted_governance_decision(
                claim.decision,
                claim.signed_artifact,
                verifier=self._verifier,
                signing_policy=self._signing_policy,
                trust_policy=self._trust_policy,
            )
            admission = validate_governed_admission(
                claim.governed_admission,
                typed_execution_request=claim.typed_execution_request,
                governance_request=claim.governance_request,
                governance_decision=decision,
                validated_at=checked_at,
            )
        except GovApiError as exc:
            raise RExecOpGovernanceDecisionError(
                "governed_attempt_untrusted",
                context=(exc.reason_code,),
            ) from exc
        if not admission.allowed or not decision.allowed:
            raise RExecOpGovernanceDecisionError(
                "governed_attempt_denied",
                context=(admission.reason_code, decision.reason_code),
            )
        expected_mode = claim.expected_actual_operation_mode
        if expected_mode not in {"apply", "recovery"}:
            raise RExecOpGovernanceDecisionError("governed_attempt_operation_mode_invalid")
        metadata = claim.governance_request.execution_facts.get("metadata")
        if not isinstance(metadata, Mapping):
            raise RExecOpGovernanceDecisionError("governed_attempt_actual_operation_mode_drift")
        if not (
            expected_mode
            == admission.actual_operation_mode
            == metadata.get("actual_operation_mode")
        ):
            raise RExecOpGovernanceDecisionError("governed_attempt_actual_operation_mode_drift")
        grant = decision.authorization
        if grant is None:
            raise RExecOpGovernanceDecisionError("governance_decision_denied")
        TrustedGovernanceDecisionConsumer._require_bindings(
            grant.as_dict(),
            claim.facts,
        )
        self._require_governance_request_bindings(
            claim.governance_request,
            claim.facts,
        )
        approval = claim.governance_request.approval_attestation
        if approval is None:
            raise RExecOpGovernanceDecisionError("governed_attempt_approval_attestation_missing")
        try:
            revoked = self._approval_revocation_port.is_revoked(
                approval.approval_id,
                approval_digest=admission.approval_attestation_digest,
                revocation_ref=approval.revocation_ref,
            )
        except Exception as exc:  # noqa: BLE001 - host lookup must fail closed
            raise RExecOpGovernanceDecisionError(
                "governed_attempt_approval_revocation_lookup_failed"
            ) from exc
        if not isinstance(revoked, bool):
            raise RExecOpGovernanceDecisionError(
                "governed_attempt_approval_revocation_lookup_invalid"
            )
        if revoked:
            raise RExecOpGovernanceDecisionError("governed_attempt_approval_revoked")

    @staticmethod
    def _require_governance_request_bindings(
        request: GovernanceRequest,
        facts: RuntimeAttemptGovernanceFacts,
    ) -> None:
        top_level = {
            "operation_id": request.operation_id,
            "step_id": request.step_id,
            "attempt_id": request.attempt_id,
            "runtime_instance_id": request.runtime_instance_id,
            "lease_id": request.lease_id,
            "lease_epoch": request.lease_epoch,
            "fencing_token_digest": request.fencing_token_digest,
            "execution_spec_digest": request.execution_spec_digest,
            "payload_digest": request.payload_digest,
            "requested_scope_digest": request.requested_scope_digest,
            "capability_inventory_digest": request.capability_inventory_digest,
            "inventory_epoch": request.capability_inventory.inventory_epoch,
        }
        expected = facts.as_dict()
        drift = [
            key for key, value in expected.items() if not _binding_equal(top_level.get(key), value)
        ]
        runtime_attempt = request.execution_facts.get("runtime_attempt")
        if not isinstance(runtime_attempt, dict):
            drift.append("execution_facts.runtime_attempt")
        else:
            drift.extend(
                f"execution_facts.runtime_attempt.{key}"
                for key, value in expected.items()
                if not _binding_equal(runtime_attempt.get(key), value)
            )
        if drift:
            raise RExecOpGovernanceDecisionError(
                "governed_attempt_binding_drift",
                context=tuple(sorted(set(drift))),
            )


def governed_attempt_binding(claim: ClaimedGovernedAttempt) -> dict[str, Any]:
    """Project the bounded governed binding used by runtime permit and receipt."""

    admission = claim.governed_admission
    return {
        "schema": GOVERNED_ATTEMPT_BINDING_SCHEMA,
        "actual_operation_mode": admission.actual_operation_mode,
        "composite_admission_digest": admission.admission_digest,
        "governance_request_digest": admission.governance_request_digest,
        "governance_decision_digest": admission.governance_decision_digest,
        "approval_attestation_digest": admission.approval_attestation_digest,
        "decision_expires_at": admission.decision_expires_at,
    }


def _governed_admission_surface() -> tuple[str, Any]:
    try:
        from govengine.typed_execution_governed_admission import (
            TYPED_EXECUTION_GOVERNED_ADMISSION_SCHEMA_VERSION,
            validate_typed_execution_governed_admission,
        )
    except (ImportError, AttributeError) as exc:
        raise RExecOpGovernanceDecisionError("governed_attempt_surface_unavailable") from exc
    if TYPED_EXECUTION_GOVERNED_ADMISSION_SCHEMA_VERSION != "v0.1":
        raise RExecOpGovernanceDecisionError("governed_attempt_surface_incompatible")
    return (
        TYPED_EXECUTION_GOVERNED_ADMISSION_SCHEMA_VERSION,
        validate_typed_execution_governed_admission,
    )


def _binding_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return hmac.compare_digest(actual, expected)
    return actual == expected
