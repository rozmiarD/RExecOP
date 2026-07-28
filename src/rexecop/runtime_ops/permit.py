from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

from rexecop.adapters.govengine_port.runtime_authority import (
    ClaimedGovernanceDecision,
    ClaimedGovernedAttempt,
    governed_attempt_binding,
)
from rexecop.catalog.digest import canonical_digest
from rexecop.errors import RExecOpValidationError
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.storage.port import RuntimeStore

EXECUTION_PERMIT_SCHEMA = "rexecop.runtime_attempt_permit.v0.1"
DEFAULT_PERMIT_TTL_SECONDS = 60.0


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class ExecutionPermitManager:
    """Bind existing admission/runtime facts; never evaluate or grant policy."""

    def __init__(self, store: RuntimeStore) -> None:
        self.store = store

    def issue(
        self,
        *,
        operation: Operation,
        plan: OperationPlan,
        step_id: str,
        attempt_id: str,
        execution_spec: dict[str, Any],
        target_binding: dict[str, Any],
        lease: dict[str, Any],
        governance_admission_digest: str,
        governance_claim: ClaimedGovernanceDecision | None = None,
        governed_claim: ClaimedGovernedAttempt | None = None,
        now: datetime | None = None,
        ttl_seconds: float = DEFAULT_PERMIT_TTL_SECONDS,
    ) -> dict[str, Any]:
        issued_at = now or _now()
        permit = {
            "schema": EXECUTION_PERMIT_SCHEMA,
            "operation_id": operation.id,
            "operation_revision": operation.operation_revision,
            "step_id": step_id,
            "attempt_id": attempt_id,
            "plan_digest": "sha256:" + canonical_digest(plan.as_dict()),
            "execution_spec_digest": str(execution_spec.get("digest") or ""),
            "govengine_decision_type": operation.govengine_decision_type,
            "governance_admission_digest": governance_admission_digest,
            "governance_binding_mode": _governance_binding_mode(
                governance_claim=governance_claim,
                governed_claim=governed_claim,
            ),
            "target_binding": target_binding,
            "target_binding_digest": "sha256:" + canonical_digest(target_binding),
            "mode": operation.mode,
            "lease_epoch": int(lease.get("lease_epoch") or 0),
            "process_instance_id": str(lease.get("process_instance_id") or ""),
            "issued_at": issued_at.isoformat(),
            "expires_at": (issued_at + timedelta(seconds=ttl_seconds)).isoformat(),
            "authority": {
                "governance": "govengine",
                "runtime_binding": "rexecop",
                "truth": "sclite",
            },
            "non_claims": [
                "This record does not evaluate policy or grant governance authority.",
                "This record is a freshness binding, not a SCLite truth artifact.",
            ],
        }
        claim = governed_claim or governance_claim
        if claim is None:
            if operation.mode not in {"observe", "dry_run", "emergency_readonly"}:
                raise RExecOpValidationError("signed_governance_decision_required_for_mutation")
        else:
            decision = claim.decision
            grant = decision.authorization
            assert grant is not None
            decision_expiry = datetime.fromisoformat(grant.expires_at)
            permit_expiry = datetime.fromisoformat(str(permit["expires_at"]))
            permit["expires_at"] = min(decision_expiry, permit_expiry).isoformat()
            permit["governance_decision"] = {
                "decision_digest": decision.decision_digest,
                "authorization_id": grant.authorization_id,
                "nonce_digest": "sha256:" + canonical_digest({"nonce": grant.nonce}),
                "requested_scope_digest": grant.requested_scope_digest,
                "capability_inventory_digest": grant.capability_inventory_digest,
                "inventory_epoch": grant.inventory_epoch,
                "policy_pack_digest": grant.policy_pack_digest,
                "policy_epoch": grant.policy_epoch,
                "signed_record_digest": claim.signed_artifact.record_digest,
                "decision_issuer_ref": claim.signed_artifact.signer_id,
            }
            if governed_claim is not None:
                governed_binding = governed_attempt_binding(governed_claim)
                _require_governed_actual_operation_mode(
                    permit_mode=str(permit["mode"]),
                    operation_mode=operation.mode,
                    governed_claim=governed_claim,
                    governed_binding=governed_binding,
                    error_prefix="execution_permit_invalid",
                )
                permit["governed_admission_binding"] = governed_binding
        permit["permit_digest"] = self.record_digest(permit)
        self.store.save_execution_permit(permit)
        return permit

    def require_fresh(
        self,
        permit: dict[str, Any],
        *,
        operation: Operation,
        plan: OperationPlan,
        attempt_id: str,
        execution_spec: dict[str, Any],
        target_binding: dict[str, Any],
        lease: dict[str, Any],
        governance_admission_digest: str,
        governance_claim: ClaimedGovernanceDecision | None = None,
        governed_claim: ClaimedGovernedAttempt | None = None,
        now: datetime | None = None,
    ) -> None:
        if permit.get("schema") != EXECUTION_PERMIT_SCHEMA:
            raise RExecOpValidationError("execution_permit_invalid: unsupported schema")
        expected_digest = self.record_digest(permit)
        if not hmac.compare_digest(str(permit.get("permit_digest") or ""), expected_digest):
            raise RExecOpValidationError("execution_permit_invalid: digest mismatch")
        expires_at = datetime.fromisoformat(str(permit["expires_at"]))
        if (now or _now()) >= expires_at:
            raise RExecOpValidationError("execution_permit_stale: permit expired")
        expected = {
            "operation_id": operation.id,
            "attempt_id": attempt_id,
            "operation_revision": operation.operation_revision,
            "plan_digest": "sha256:" + canonical_digest(plan.as_dict()),
            "execution_spec_digest": str(execution_spec.get("digest") or ""),
            "govengine_decision_type": operation.govengine_decision_type,
            "governance_admission_digest": governance_admission_digest,
            "target_binding_digest": "sha256:" + canonical_digest(target_binding),
            "mode": operation.mode,
            "lease_epoch": int(lease.get("lease_epoch") or 0),
            "process_instance_id": str(lease.get("process_instance_id") or ""),
        }
        drift = [key for key, value in expected.items() if permit.get(key) != value]
        if drift:
            raise RExecOpValidationError(
                "execution_permit_stale: binding drift: " + ",".join(sorted(drift))
            )
        self.store.validate_execution_lease(lease)
        expected_mode = _governance_binding_mode(
            governance_claim=governance_claim,
            governed_claim=governed_claim,
        )
        if permit.get("governance_binding_mode") != expected_mode:
            raise RExecOpValidationError("execution_permit_stale: governance binding mode drift")
        claim = governed_claim or governance_claim
        if claim is not None:
            decision = claim.decision
            grant = decision.authorization
            assert grant is not None
            if (now or _now()) >= datetime.fromisoformat(grant.expires_at):
                raise RExecOpValidationError("execution_permit_stale: governance decision expired")
            binding = permit.get("governance_decision")
            if not isinstance(binding, dict):
                raise RExecOpValidationError(
                    "execution_permit_invalid: missing governance decision binding"
                )
            governed_expected = {
                "decision_digest": decision.decision_digest,
                "authorization_id": grant.authorization_id,
                "nonce_digest": "sha256:" + canonical_digest({"nonce": grant.nonce}),
                "requested_scope_digest": grant.requested_scope_digest,
                "capability_inventory_digest": grant.capability_inventory_digest,
                "inventory_epoch": grant.inventory_epoch,
                "policy_pack_digest": grant.policy_pack_digest,
                "policy_epoch": grant.policy_epoch,
                "signed_record_digest": claim.signed_artifact.record_digest,
                "decision_issuer_ref": claim.signed_artifact.signer_id,
            }
            governed_drift = [
                key for key, value in governed_expected.items() if binding.get(key) != value
            ]
            if governed_drift:
                raise RExecOpValidationError(
                    "execution_permit_stale: governance drift: " + ",".join(sorted(governed_drift))
                )
            if governed_claim is not None:
                expected_governed_binding = governed_attempt_binding(governed_claim)
                _require_governed_actual_operation_mode(
                    permit_mode=str(permit.get("mode") or ""),
                    operation_mode=operation.mode,
                    governed_claim=governed_claim,
                    governed_binding=expected_governed_binding,
                    error_prefix="execution_permit_stale",
                )
                if permit.get("governed_admission_binding") != expected_governed_binding:
                    raise RExecOpValidationError("execution_permit_stale: governed admission drift")
        if str(target_binding.get("target") or "") != operation.target:
            raise RExecOpValidationError("execution_permit_stale: target binding drift")

    @staticmethod
    def record_digest(permit: dict[str, Any]) -> str:
        """Recompute the runtime-owned permit digest from its bounded record."""

        payload = {key: value for key, value in permit.items() if key != "permit_digest"}
        return "sha256:" + canonical_digest(payload)


def _governance_binding_mode(
    *,
    governance_claim: ClaimedGovernanceDecision | None,
    governed_claim: ClaimedGovernedAttempt | None,
) -> str:
    if governance_claim is not None and governed_claim is not None:
        raise RExecOpValidationError("execution_permit_invalid: multiple governance claims")
    if governed_claim is not None:
        return "governed_attempt"
    if governance_claim is not None:
        return "signed_decision"
    return "legacy_read_only"


def _require_governed_actual_operation_mode(
    *,
    permit_mode: str,
    operation_mode: str,
    governed_claim: ClaimedGovernedAttempt,
    governed_binding: dict[str, Any],
    error_prefix: str,
) -> None:
    expected_mode = governed_claim.expected_actual_operation_mode
    if not (
        permit_mode
        == operation_mode
        == expected_mode
        == governed_binding.get("actual_operation_mode")
    ):
        raise RExecOpValidationError(
            f"{error_prefix}: governed actual operation mode drift"
        )
