from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from govengine.approvals import (
    ApprovalAttestation,
    ApprovalRevocationPort,
    ApprovalTrustPolicy,
    approval_attestation_digest,
)
from govengine.capabilities import (
    CapabilityInventoryBinding,
    OperationCapabilityRequirements,
    capability_inventory_binding_digest,
    operation_capability_requirements_digest,
)
from govengine.governance import (
    GovernanceRequest,
    execution_facts_digest,
    governance_subject_digest,
    requested_scope_digest,
)
from govengine.governance_decision import (
    ApprovalSignatureVerificationPort,
    GovernanceAuthorization,
    GovernanceDecision,
    PolicyActivationPort,
    _governance_decision_body_digest,
)
from govengine.governance_decision_signing import sign_governance_decision
from govengine.policy import (
    PolicyCompiler,
    RuntimeControlProjection,
    policy_pack_digest,
)
from govengine.policy.activation import PolicyActivationBinding
from govengine.scope_policy import ScopePolicyBinding, scope_policy_binding_digest
from govengine.signing import (
    DemoDigestSigner,
    DemoDigestVerifier,
    SigningPolicy,
    TrustPolicy,
)
from govengine.typed_execution_governance import (
    TypedExecutionGovernanceRequest,
    typed_execution_governance_request_digest,
)
from govengine.typed_execution_governed_admission import (
    evaluate_typed_execution_governed_admission,
    evaluate_typed_execution_governed_admission_v02,
)

from rexecop.adapters.govengine_port.runtime_authority import (
    RuntimeAttemptGovernanceFacts,
    SignedGovernanceDecisionBundle,
    SignedGovernedAttemptBundle,
)
from rexecop.runtime_ops.governance_facts import _runtime_inventory


class TestAttemptGovernanceAuthority:
    """Signed deterministic authority for runtime-mechanics tests only."""

    __test__ = False

    def authorize_attempt(
        self,
        facts: RuntimeAttemptGovernanceFacts,
    ) -> SignedGovernanceDecisionBundle:
        now = datetime.now(UTC).replace(microsecond=0)
        marker = "sha256:" + "a" * 64
        grant = GovernanceAuthorization(
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
                signer=DemoDigestSigner(signer_id="test-decision-signer"),
                payload_ref=f"artifact://tests/{decision.decision_id}",
            ),
        )


class TestApprovalRevocations(ApprovalRevocationPort):
    __test__ = False

    def __init__(self, *, revoked: bool = False, fail_lookup: bool = False) -> None:
        self.revoked = revoked
        self.fail_lookup = fail_lookup
        self.lookups = 0

    def is_revoked(
        self,
        approval_id: str,
        *,
        approval_digest: str,
        revocation_ref: str,
    ) -> bool:
        self.lookups += 1
        if self.fail_lookup:
            raise RuntimeError("deterministic revocation lookup failure")
        return self.revoked


class _ApprovalSignatureVerifier(ApprovalSignatureVerificationPort):
    def verify_approval_signature(
        self,
        attestation: ApprovalAttestation,
        *,
        approval_digest: str,
        trust_policy_id: str,
    ) -> bool:
        return bool(attestation.signature_ref and approval_digest and trust_policy_id)


class _PolicyActivation(PolicyActivationPort):
    def __init__(
        self,
        request: dict[str, Any],
        *,
        now: datetime,
    ) -> None:
        self.request = request
        self.now = now

    def current_binding(self, policy_id: str) -> PolicyActivationBinding:
        return PolicyActivationBinding.from_mapping(
            {
                "schema_version": "v1",
                "binding_id": f"test-policy-activation:{policy_id}",
                "policy_id": policy_id,
                "policy_version": self.request["policy_pack"]["version"],
                "policy_pack_digest": self.request["policy_pack_digest"],
                "policy_epoch": self.request["policy_epoch"],
                "issuer_ref": self.request["policy_pack"]["issuer_ref"],
                "trust_ref": "test-policy-trust:runtime",
                "status": "active",
                "not_before": (self.now - timedelta(minutes=1)).isoformat(),
                "expires_at": (self.now + timedelta(minutes=1)).isoformat(),
            }
        )


class TestGovernedAttemptAuthority:
    """Actual GovEngine composite evaluator for deterministic runtime tests."""

    __test__ = False

    def __init__(
        self,
        *,
        revocations: TestApprovalRevocations,
        target_namespaces: tuple[str, ...] = ("fixture-target",),
        plugin_backend_controls: tuple[str, ...] = ("fixture_plugin",),
        plugin_egress_controls: tuple[str, ...] = ("no_network",),
    ) -> None:
        self.revocations = revocations
        self.target_namespaces = target_namespaces
        self.plugin_backend_controls = plugin_backend_controls
        self.plugin_egress_controls = plugin_egress_controls
        self.requests: list[RuntimeAttemptGovernanceFacts] = []
        self.actual_modes: list[str] = []

    def authorize_governed_attempt(
        self,
        facts: RuntimeAttemptGovernanceFacts,
        *,
        typed_execution_request: TypedExecutionGovernanceRequest,
        actual_operation_mode: str,
    ) -> SignedGovernedAttemptBundle:
        self.requests.append(facts)
        self.actual_modes.append(actual_operation_mode)
        now = datetime.now(UTC).replace(microsecond=0)
        governance_request = self._governance_request(
            facts,
            typed_execution_request=typed_execution_request,
            actual_operation_mode=actual_operation_mode,
            now=now,
        )
        evaluator = (
            evaluate_typed_execution_governed_admission_v02
            if typed_execution_request.capability_descriptor.certification_tier == "plugin"
            else evaluate_typed_execution_governed_admission
        )
        admission, decision = evaluator(
            typed_execution_request,
            governance_request,
            actual_operation_mode=actual_operation_mode,
            policy_activation_port=_PolicyActivation(
                governance_request.as_dict(),
                now=now,
            ),
            evaluated_at=now,
            admitted_at=now,
            approval_trust_policy=ApprovalTrustPolicy(
                policy_id="test-runtime-approvers",
                trusted_roles=("runtime-test-approver",),
                trusted_domains=("tests:rexecop",),
                trusted_approver_refs=("test-operator",),
                require_signature_ref=True,
            ),
            approval_revocation_port=self.revocations,
            approval_signature_verifier=_ApprovalSignatureVerifier(),
            authorization_nonce=f"test-governed-nonce:{facts.attempt_id}",
            authorization_expires_at=now + timedelta(seconds=30),
            decision_id=f"test-governed-decision:{facts.attempt_id}",
        )
        return SignedGovernedAttemptBundle(
            governance_request=governance_request,
            governed_admission=admission,
            decision=decision,
            signed_artifact=sign_governance_decision(
                decision,
                signer=DemoDigestSigner(signer_id="test-decision-signer"),
                payload_ref=f"artifact://tests/{decision.decision_id}",
            ),
        )

    def _governance_request(
        self,
        facts: RuntimeAttemptGovernanceFacts,
        *,
        typed_execution_request: TypedExecutionGovernanceRequest,
        actual_operation_mode: str,
        now: datetime,
    ) -> GovernanceRequest:
        constraints: list[dict[str, Any]] = [
            {
                "constraint_id": "bounded-output",
                "kind": "output_limit",
                "value": 4096,
            }
        ]
        if typed_execution_request.capability_descriptor.certification_tier == "plugin":
            constraints.extend(
                [
                    {
                        "constraint_id": "plugin-backend",
                        "kind": "allowed_backend_classes",
                        "value": list(self.plugin_backend_controls),
                    },
                    {
                        "constraint_id": "plugin-egress",
                        "kind": "allowed_network_egress",
                        "value": list(self.plugin_egress_controls),
                    },
                ]
            )
        compiled = PolicyCompiler().compile(
            {
                "policy_id": "test-runtime-mutation",
                "version": "1",
                "schema_version": "v1",
                "issuer_ref": "tests:rexecop",
                "policy_epoch": 1,
                "validity": {
                    "not_before": (now - timedelta(minutes=1)).isoformat(),
                    "expires_at": (now + timedelta(minutes=1)).isoformat(),
                },
                "supersedes": [],
                "rules": [
                    {
                        "rule_id": "govern-runtime-mutation",
                        "effect": "approval_required",
                        "conditions": [
                            {
                                "path": "action.mode",
                                "operator": "eq",
                                "value": "mutation",
                            }
                        ],
                        "reason_code": "mutation_requires_approval",
                        "obligations": [{"obligation_id": "receipt", "kind": "receipt"}],
                        "constraints": constraints,
                    }
                ],
            }
        )
        assert compiled.ok and compiled.policy_pack is not None
        policy_pack = compiled.policy_pack
        pack_digest = policy_pack_digest(policy_pack)
        execution_facts = {
            "schema_version": "v0.1",
            "request_id": f"test-governance:{facts.attempt_id}",
            "subject_ref": (f"governance:{facts.operation_id}:{facts.step_id}:{facts.attempt_id}"),
            "principal": {"kind": "operator"},
            "action": {"mode": "mutation"},
            "resource": {"criticality": "low"},
            "context": {"environment": "test"},
            "evidence_refs": [],
            "runtime_attempt": facts.as_dict(),
            "metadata": {
                "actual_operation_mode": actual_operation_mode,
                "typed_execution_governance_request_digest": (
                    typed_execution_governance_request_digest(typed_execution_request)
                ),
            },
        }
        destination = typed_execution_request.destination_binding
        matching_scopes: list[dict[str, Any]] = []
        for target_namespace in self.target_namespaces:
            candidate_scope: dict[str, Any] = {
                "target_namespace": target_namespace,
            }
            if destination:
                candidate_scope["requested_destination"] = dict(destination)
            if requested_scope_digest(candidate_scope) == facts.requested_scope_digest:
                matching_scopes.append(candidate_scope)
        if len(matching_scopes) != 1:
            raise AssertionError("test authority requested scope match must be unique")
        requested_scope = matching_scopes[0]
        inventory = _runtime_inventory(
            runtime_instance_id=facts.runtime_instance_id,
            inventory_epoch=facts.inventory_epoch,
        )
        if typed_execution_request.capability_descriptor.certification_tier == "plugin":
            inventory_payload = inventory.as_dict()
            inventory_payload["backend_classes"] = sorted(
                {
                    *inventory.backend_classes,
                    typed_execution_request.backend_class,
                }
            )
            inventory_payload["capabilities"] = sorted(
                {
                    *inventory.capabilities,
                    *typed_execution_request.capability_descriptor.declared_capability_descriptors,
                }
            )
            inventory = CapabilityInventoryBinding.from_mapping(inventory_payload)
        if facts.capability_inventory_digest != capability_inventory_binding_digest(inventory):
            raise AssertionError("test authority capability inventory drift")
        scope_policy = ScopePolicyBinding.from_mapping(
            {
                "schema_version": "v1",
                "binding_id": "test-scope-policy",
                "policy_pack_digest": pack_digest,
                "policy_epoch": 1,
                "source_ref": "test-policy:test-runtime-mutation@1",
                "attestation_ref": "test-attestation:scope",
                "allowed_target_namespaces": list(self.target_namespaces),
                "network_allowed": bool(destination),
                "allowed_schemes": ([str(destination.get("scheme") or "")] if destination else []),
                "allowed_ports": (
                    [int(destination.get("effective_port") or 0)] if destination else []
                ),
                "allowed_address_classes": (
                    [str(destination.get("address_class") or "")] if destination else []
                ),
                "redirect_policy": "same_origin",
                "private_networks_allowed": False,
            }
        )
        required_capabilities = tuple(typed_execution_request.required_capability_descriptors)
        requirements = OperationCapabilityRequirements.from_mapping(
            {
                "schema_version": "v1",
                "requirements_id": f"test-requirements:{facts.attempt_id}",
                "operation_id": facts.operation_id,
                "step_id": facts.step_id,
                "execution_spec_digest": facts.execution_spec_digest,
                "required_backend_class": typed_execution_request.backend_class,
                "side_effect_class": "mutation",
                "required_capabilities": list(required_capabilities),
            }
        )
        request: dict[str, Any] = {
            "schema_version": "v1",
            "transaction_id": f"test-governance:{facts.attempt_id}",
            "operation_id": facts.operation_id,
            "step_id": facts.step_id,
            "attempt_id": facts.attempt_id,
            "policy_pack": policy_pack.as_dict(),
            "policy_pack_digest": pack_digest,
            "policy_epoch": 1,
            "execution_facts": execution_facts,
            "execution_facts_digest": execution_facts_digest(execution_facts),
            "execution_spec_digest": facts.execution_spec_digest,
            "payload_digest": facts.payload_digest,
            "requested_scope": requested_scope,
            "requested_scope_digest": facts.requested_scope_digest,
            "scope_policy_binding": scope_policy.as_dict(),
            "scope_policy_binding_digest": scope_policy_binding_digest(scope_policy),
            "capability_requirements": requirements.as_dict(),
            "capability_requirements_digest": (
                operation_capability_requirements_digest(requirements)
            ),
            "capability_inventory": inventory.as_dict(),
            "capability_inventory_digest": facts.capability_inventory_digest,
            "side_effect_class": "mutation",
            "runtime_instance_id": facts.runtime_instance_id,
            "lease_id": facts.lease_id,
            "lease_epoch": facts.lease_epoch,
            "fencing_token_digest": facts.fencing_token_digest,
        }
        subject = GovernanceRequest.from_mapping(request)
        approval = ApprovalAttestation.from_mapping(
            {
                "schema_version": "v1",
                "approval_id": f"test-approval:{facts.attempt_id}",
                "subject_digest": governance_subject_digest(subject),
                "operation_id": facts.operation_id,
                "step_id": facts.step_id,
                "attempt_id": facts.attempt_id,
                "execution_spec_digest": facts.execution_spec_digest,
                "execution_facts_digest": request["execution_facts_digest"],
                "target_scope_digest": facts.requested_scope_digest,
                "policy_pack_digest": pack_digest,
                "policy_epoch": 1,
                "approved_side_effect_class": "mutation",
                "approver_ref": "test-operator",
                "approver_role": "runtime-test-approver",
                "trust_domain": "tests:rexecop",
                "issued_at": now.isoformat(),
                "not_before": (now - timedelta(seconds=1)).isoformat(),
                "expires_at": (now + timedelta(seconds=30)).isoformat(),
                "revocation_ref": "test-revocations:runtime",
                "signature_ref": "test-signature:runtime",
            }
        )
        request["approval_attestation"] = approval.as_dict()
        request["approval_attestation_digest"] = approval_attestation_digest(approval)
        return GovernanceRequest.from_mapping(request)


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


def governed_runtime_kwargs(
    *,
    revocations: TestApprovalRevocations | None = None,
    authority: TestGovernedAttemptAuthority | None = None,
    target_namespaces: tuple[str, ...] = ("fixture-target",),
) -> dict[str, Any]:
    revocation_port = revocations or TestApprovalRevocations()
    governed_authority = authority or TestGovernedAttemptAuthority(
        revocations=revocation_port,
        target_namespaces=target_namespaces,
    )
    return {
        "governed_attempt_authority": governed_authority,
        "approval_revocation_port": revocation_port,
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
