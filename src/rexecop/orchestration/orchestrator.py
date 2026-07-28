from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from govengine.receipt_conformance import (
    build_runtime_receipt_binding,
    evaluate_receipt_conformance,
)
from govengine.typed_execution_governance import (
    typed_execution_governance_request_digest,
)

from rexecop.adapters.govengine_port.contracts import (
    BLOCKING_DECISIONS,
    WAITING_DECISIONS,
    is_mutating_mode,
)
from rexecop.adapters.govengine_port.runtime_authority import (
    ClaimedGovernanceDecision,
    ClaimedGovernedAttempt,
    TrustedGovernanceDecisionConsumer,
    TrustedGovernedAttemptConsumer,
)
from rexecop.catalog.digest import canonical_digest
from rexecop.connectors.composite_runtime import build_connector_runtime
from rexecop.connectors.runtime import ConnectorDispatcher
from rexecop.errors import (
    RExecOpOutcomeIndeterminate,
    RExecOpStateError,
    RExecOpValidationError,
)
from rexecop.escalation.package import build_escalation_package
from rexecop.evidence.event import EvidenceEventType
from rexecop.evidence.manager import EvidenceManager
from rexecop.evidence.public_projection import resolve_public_projection_allowlist
from rexecop.execution.backend import StepExecutionContext, StepExecutionResult
from rexecop.execution.executor import StepExecutor
from rexecop.execution.govengine_governance import (
    build_governed_typed_execution_precheck_request,
    typed_execution_governance_overlay,
)
from rexecop.observability.emitter import StructuredLogEmitter
from rexecop.observability.structured_log import StructuredLogRefs
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.operation.state import OperationState
from rexecop.policy.enforcement import (
    execution_policy_binding,
    validate_policy_enforcement_record,
)
from rexecop.policy.pack import compile_environment_policy_pack
from rexecop.profile.loader import LoadedProfile, load_profile
from rexecop.runtime_ops.governance_facts import build_runtime_attempt_governance_facts
from rexecop.runtime_ops.permit import ExecutionPermitManager
from rexecop.runtime_ops.rollback import rollback_failure_authority_digest
from rexecop.storage.port import RuntimeStore
from rexecop.validation.validator import validate_operation_result
from rexecop.workflow.runner import WorkflowRunner

READ_ONLY_MODES = frozenset({"dry_run", "observe", "emergency_readonly"})
INTRINSICALLY_NON_RETRYABLE_ERRORS = frozenset({"outcome_indeterminate"})
TERMINAL_STATES = frozenset(
    {
        OperationState.COMPLETED.value,
        OperationState.FAILED.value,
        OperationState.CANCELLED.value,
        OperationState.ESCALATED.value,
        OperationState.BLOCKED.value,
    }
)


def _receipt_failure(
    result: StepExecutionResult,
    reason_code: str,
) -> StepExecutionResult:
    output = dict(result.output)
    output["error_class"] = "receipt_postcondition_failed"
    output["receipt_reason_code"] = reason_code
    return replace(
        result,
        success=False,
        output=output,
        error=f"runtime receipt postcondition failed: {reason_code}",
    )


class _WorkflowEvidenceSink:
    def __init__(self, orchestrator: OperationOrchestrator, operation: Operation) -> None:
        self._orchestrator = orchestrator
        self._operation = operation

    def on_step_started(self, *, step_id: str, correlation_id: str) -> None:
        self._operation.current_step_id = step_id
        self._orchestrator._emit_step_event(
            self._operation,
            EvidenceEventType.STEP_STARTED,
            step_id=step_id,
            correlation_id=correlation_id,
            payload={"step_id": step_id},
        )

    def on_step_completed(
        self,
        *,
        step_id: str,
        result: StepExecutionResult,
        correlation_id: str,
    ) -> None:
        payload = result.as_dict()
        self._orchestrator._emit_step_event(
            self._operation,
            EvidenceEventType.STEP_COMPLETED,
            step_id=step_id,
            correlation_id=correlation_id,
            payload=payload,
        )

    def on_step_failed(
        self,
        *,
        step_id: str,
        result: StepExecutionResult,
        correlation_id: str,
    ) -> None:
        self._orchestrator._emit_step_event(
            self._operation,
            EvidenceEventType.STEP_FAILED,
            step_id=step_id,
            correlation_id=correlation_id,
            payload=result.as_dict(),
        )


class OperationOrchestrator:
    def __init__(
        self,
        *,
        store: RuntimeStore,
        evidence: EvidenceManager,
        structured_log: StructuredLogEmitter | None = None,
        transition: Any,
        export_receipt: Any,
        auto_reaction_handler: Callable[[Operation], dict[str, Any] | None] | None = None,
        governance_decision_consumer: TrustedGovernanceDecisionConsumer | None = None,
        governed_attempt_consumer: TrustedGovernedAttemptConsumer | None = None,
        catalog_binding_validator: Callable[[Operation, OperationPlan], None] | None = None,
        inventory_epoch: int = 0,
    ) -> None:
        self.store = store
        self.evidence = evidence
        self.structured_log = structured_log
        self._transition = transition
        self._export_receipt = export_receipt
        self._auto_reaction_handler = auto_reaction_handler
        self._governance_decision_consumer = governance_decision_consumer
        self._governed_attempt_consumer = governed_attempt_consumer
        self._catalog_binding_validator = catalog_binding_validator
        self._inventory_epoch = inventory_epoch
        self.execution_lease_record: dict[str, Any] | None = None
        self.permits = ExecutionPermitManager(store)

    def _runner_for_operation(self, operation: Operation) -> WorkflowRunner:
        connectors = operation.metadata.get("environment_connectors")
        if not isinstance(connectors, dict):
            connectors = {}
        policy_raw = operation.metadata.get("policy_pack")
        policy_pack = (
            compile_environment_policy_pack(policy_raw) if isinstance(policy_raw, dict) else None
        )
        target_criticality = str(operation.metadata.get("target_criticality") or "low")
        runtime = build_connector_runtime(
            connectors=connectors,
            profile_root=operation.metadata.get("profile_root"),
            mutating_allowed=self._allows_mutating(operation),
            policy_pack=policy_pack,
            operation_id=operation.id,
            target_criticality=target_criticality,
        )
        executor = StepExecutor(
            connector_dispatcher=ConnectorDispatcher(runtime),
            evidence_handler=lambda ctx: self._export_receipt(ctx.operation_id),
            attempt_start_handler=self._start_attempt,
            attempt_pre_io_handler=self._require_attempt_fresh,
            attempt_finish_handler=self._finish_attempt,
            attempt_receipt_handler=self._bind_attempt_receipt,
            governed_attempt_enabled=self._governed_attempt_consumer is not None,
        )
        return WorkflowRunner(executor)

    def _start_attempt(
        self, context: StepExecutionContext, spec: dict[str, Any] | None
    ) -> dict[str, Any]:
        lease = self.execution_lease_record
        if lease is None:
            raise RExecOpValidationError("execution attempt requires active execution lease")
        self.store.validate_execution_lease(lease)
        operation = self.store.load_operation(context.operation_id)
        plan = self.preflight_rollback_authority(operation)
        if self._is_rollback_operation(operation) and self._governed_attempt_consumer is None:
            raise RExecOpValidationError(
                "rollback connector attempt requires governed approval authority"
            )
        governance = operation.metadata.get("governance_admission")
        governance_admission_digest = ""
        if isinstance(governance, dict):
            governance_admission_digest = str(governance.get("admission_digest") or "")
        typed_admissions = context.shared_state.get("typed_execution_admissions")
        step_admission = None
        if isinstance(typed_admissions, dict):
            step_admission = typed_admissions.get(str(context.step.get("id") or ""))
            if isinstance(step_admission, dict):
                governance_admission_digest = str(
                    step_admission.get("admission_digest") or governance_admission_digest
                )
        execution_spec = dict(spec or {})
        mutating = is_mutating_mode(context.mode)
        typed_request = None
        if mutating:
            if self._governed_attempt_consumer is None:
                raise RExecOpValidationError("governed approval authority required for mutation")
            if not isinstance(step_admission, dict):
                raise RExecOpValidationError("governed mutation requires typed execution precheck")
            typed_request = build_governed_typed_execution_precheck_request(
                spec=execution_spec,
                operation_id=operation.id,
                actual_operation_mode=context.mode,
                shared_state=context.shared_state,
            )
            request_digest = typed_execution_governance_request_digest(typed_request)
            if not hmac.compare_digest(
                str(step_admission.get("request_digest") or ""),
                request_digest,
            ):
                raise RExecOpValidationError("typed execution governed precheck request drift")
            if (
                step_admission.get("actual_operation_mode") != context.mode
                or step_admission.get("typed_execution_operation_mode") != "apply"
            ):
                raise RExecOpValidationError("typed execution governed precheck mode drift")
        attempt_id = self.store.allocate_execution_attempt_id()
        governance_claim = None
        governed_claim = None
        if mutating:
            assert self._governed_attempt_consumer is not None
            assert typed_request is not None
            facts = build_runtime_attempt_governance_facts(
                operation_id=operation.id,
                step_id=str(context.step.get("id") or ""),
                attempt_id=attempt_id,
                target=context.target,
                execution_spec=execution_spec,
                lease=lease,
                inventory_epoch=self._inventory_epoch,
            )
            governed_claim = self._governed_attempt_consumer.authorize_and_claim(
                facts,
                typed_execution_request=typed_request,
                actual_operation_mode=context.mode,
            )
            governance_admission_digest = governed_claim.governed_admission.admission_digest
            current_facts = build_runtime_attempt_governance_facts(
                operation_id=operation.id,
                step_id=str(context.step.get("id") or ""),
                attempt_id=attempt_id,
                target=context.target,
                execution_spec=execution_spec,
                lease=lease,
                inventory_epoch=self._inventory_epoch,
            )
            if current_facts != facts:
                raise RExecOpValidationError("governance_runtime_facts_drift")
        elif self._governance_decision_consumer is not None:
            facts = build_runtime_attempt_governance_facts(
                operation_id=operation.id,
                step_id=str(context.step.get("id") or ""),
                attempt_id=attempt_id,
                target=context.target,
                execution_spec=execution_spec,
                lease=lease,
                inventory_epoch=self._inventory_epoch,
            )
            governance_claim = self._governance_decision_consumer.authorize_and_claim(facts)
            current_facts = build_runtime_attempt_governance_facts(
                operation_id=operation.id,
                step_id=str(context.step.get("id") or ""),
                attempt_id=attempt_id,
                target=context.target,
                execution_spec=execution_spec,
                lease=lease,
                inventory_epoch=self._inventory_epoch,
            )
            if current_facts != facts:
                raise RExecOpValidationError("governance_runtime_facts_drift")
        execution_payload = execution_spec.get("payload")
        destination = (
            execution_payload.get("destination_binding")
            if isinstance(execution_payload, dict)
            else None
        )
        target_binding = {
            "target": context.target,
            "destination": dict(destination) if isinstance(destination, dict) else {},
        }
        permit = self.permits.issue(
            operation=operation,
            plan=plan,
            step_id=str(context.step.get("id") or ""),
            attempt_id=attempt_id,
            execution_spec=execution_spec,
            target_binding=target_binding,
            lease=lease,
            governance_admission_digest=governance_admission_digest,
            governance_claim=governance_claim,
            governed_claim=governed_claim,
        )
        attempt = self.store.start_execution_attempt(
            operation_id=operation.id,
            attempt_id=attempt_id,
            operation_revision=operation.operation_revision,
            step_id=str(context.step.get("id") or ""),
            plan=plan.as_dict(),
            execution_spec=execution_spec,
            target=context.target,
            mode=context.mode,
            lease=lease,
            execution_permit_ref=str(permit["permit_digest"]),
        )
        attempt["_runtime_permit"] = permit
        attempt["_execution_spec"] = execution_spec
        attempt["_target_binding"] = target_binding
        attempt["_governance_admission_digest"] = governance_admission_digest
        if governance_claim is not None:
            # These objects live only until the immediate post-I/O conformance
            # check. The durable attempt and permit remain JSON-only records.
            attempt["_governance_claim"] = governance_claim
        if governed_claim is not None:
            attempt["_governed_claim"] = governed_claim
            typed_specs = context.shared_state.get("typed_execution_specs")
            if isinstance(typed_specs, dict):
                typed_binding = typed_specs.get(str(context.step.get("id") or ""))
                if isinstance(typed_binding, dict):
                    typed_binding["governed_admission_binding"] = dict(
                        permit["governed_admission_binding"]
                    )
        return attempt

    def _require_attempt_fresh(self, attempt: dict[str, Any]) -> None:
        """Revalidate the durable attempt's authority immediately before connector I/O."""

        lease = self.execution_lease_record
        if lease is None:
            raise RExecOpValidationError("execution attempt requires active execution lease")
        operation = self.store.load_operation(str(attempt["operation_id"]))
        plan = self.preflight_rollback_authority(operation)
        permit = attempt.get("_runtime_permit")
        execution_spec = attempt.get("_execution_spec")
        target_binding = attempt.get("_target_binding")
        if not isinstance(permit, dict):
            raise RExecOpValidationError("execution permit missing before connector I/O")
        if not isinstance(execution_spec, dict) or not isinstance(target_binding, dict):
            raise RExecOpValidationError("execution attempt binding missing before connector I/O")
        claim = attempt.get("_governance_claim")
        governance_claim = claim if isinstance(claim, ClaimedGovernanceDecision) else None
        raw_governed_claim = attempt.get("_governed_claim")
        governed_claim = (
            raw_governed_claim if isinstance(raw_governed_claim, ClaimedGovernedAttempt) else None
        )
        if governed_claim is not None:
            current_facts = build_runtime_attempt_governance_facts(
                operation_id=operation.id,
                step_id=str(attempt["step_id"]),
                attempt_id=str(attempt["attempt_id"]),
                target=str(target_binding.get("target") or ""),
                execution_spec=execution_spec,
                lease=lease,
                inventory_epoch=self._inventory_epoch,
            )
            if current_facts != governed_claim.facts:
                raise RExecOpValidationError("governance_runtime_facts_drift")
        self.permits.require_fresh(
            permit,
            operation=operation,
            plan=plan,
            attempt_id=str(attempt["attempt_id"]),
            execution_spec=execution_spec,
            target_binding=target_binding,
            lease=lease,
            governance_admission_digest=str(attempt.get("_governance_admission_digest") or ""),
            governance_claim=governance_claim,
            governed_claim=governed_claim,
        )
        if governed_claim is not None:
            if self._governed_attempt_consumer is None:
                raise RExecOpValidationError(
                    "governed approval authority missing before connector I/O"
                )
            self._governed_attempt_consumer.require_current(governed_claim)

    def _finish_attempt(
        self,
        attempt: dict[str, Any],
        status: str,
        result: StepExecutionResult | None,
    ) -> None:
        payload = result.as_dict() if result is not None else {}
        error_class = (
            "outcome_indeterminate"
            if status == "indeterminate"
            else str((payload.get("output") or {}).get("error_class") or "")
        )
        result_digest = ("sha256:" + canonical_digest(payload)) if payload else ""
        try:
            if status == "indeterminate":
                finished = self.store.finish_indeterminate_if_started(
                    attempt,
                    result_digest=result_digest,
                )
            else:
                finished = self.store.finish_execution_attempt(
                    attempt,
                    status=status,
                    result_digest=result_digest,
                    error_class=error_class,
                )
        except Exception:  # noqa: BLE001 - consume side-effectful finalization uncertainty
            if attempt.get("side_effectful") is not True:
                raise
            if status != "indeterminate":
                try:
                    finished = self.store.finish_indeterminate_if_started(
                        attempt,
                        result_digest=result_digest,
                    )
                except Exception:  # noqa: BLE001 - leave started record for recovery
                    pass
                else:
                    attempt.update(finished)
            raise RExecOpOutcomeIndeterminate(
                "side-effectful attempt finalization requires reconciliation"
            ) from None
        attempt.update(finished)

    def _bind_attempt_receipt(
        self,
        attempt: dict[str, Any],
        result: StepExecutionResult,
    ) -> StepExecutionResult:
        claim = attempt.get("_governed_claim") or attempt.get("_governance_claim")
        permit = attempt.get("_runtime_permit")
        if not isinstance(
            claim,
            (ClaimedGovernanceDecision, ClaimedGovernedAttempt),
        ) or not isinstance(permit, dict):
            return result
        permit_digest = self.permits.record_digest(permit)
        if not hmac.compare_digest(
            str(permit.get("permit_digest") or ""),
            permit_digest,
        ):
            return _receipt_failure(result, "runtime_permit_digest_mismatch")
        decision = claim.decision
        grant = decision.authorization
        if grant is None:
            return _receipt_failure(result, "governance_authorization_missing")
        output_digests_raw = result.output.get("output_digests")
        output_digests = (
            {str(key): str(value) for key, value in output_digests_raw.items()}
            if isinstance(output_digests_raw, dict)
            else {}
        )
        output_sizes = result.output.get("output_sizes")
        output_bytes = (
            int(output_sizes.get("record_bytes") or 0) if isinstance(output_sizes, dict) else 0
        )
        binding = build_runtime_receipt_binding(
            receipt_id=f"runtime-receipt:{attempt['attempt_id']}",
            operation_id=str(attempt["operation_id"]),
            step_id=str(attempt["step_id"]),
            attempt_id=str(attempt["attempt_id"]),
            runtime_instance_id=claim.facts.runtime_instance_id,
            decision_digest=decision.decision_digest,
            runtime_permit_digest=permit_digest,
            lease_id=claim.facts.lease_id,
            lease_epoch=claim.facts.lease_epoch,
            fencing_token_digest=claim.facts.fencing_token_digest,
            execution_spec_digest=claim.facts.execution_spec_digest,
            payload_digest=claim.facts.payload_digest,
            requested_scope_digest=claim.facts.requested_scope_digest,
            capability_inventory_digest=claim.facts.capability_inventory_digest,
            inventory_epoch=claim.facts.inventory_epoch,
            policy_pack_digest=grant.policy_pack_digest,
            policy_epoch=grant.policy_epoch,
            terminal_status="completed" if result.success else "failed",
            output_digests=output_digests,
            output_bytes=output_bytes,
        )
        conformance = evaluate_receipt_conformance(
            decision,
            binding,
            expected_runtime_permit_digest=permit_digest,
        )
        if not conformance.conformant:
            failed = _receipt_failure(result, conformance.reason_code)
            return replace(
                failed,
                runtime_receipt_binding=binding.as_dict(),
                receipt_conformance=conformance.as_dict(),
            )
        return replace(
            result,
            runtime_receipt_binding=binding.as_dict(),
            receipt_conformance=conformance.as_dict(),
        )

    def start(self, operation_id: str) -> Operation:
        operation = self.store.load_operation(operation_id)
        self._prepare_start(operation)
        return self._continue_execution(operation_id)

    def advance(self, operation_id: str, *, max_steps: int = 1) -> Operation:
        operation = self.store.load_operation(operation_id)
        if operation.state in {
            OperationState.APPROVED.value,
            OperationState.RESUMING.value,
            OperationState.RETRYING.value,
        }:
            self._prepare_start(operation)
        return self._continue_execution(operation_id, max_steps=max_steps)

    def pause(self, operation_id: str) -> Operation:
        operation = self.store.load_operation(operation_id)
        if operation.state != OperationState.RUNNING.value:
            raise RExecOpValidationError(f"pause requires running operation, got {operation.state}")
        plan = self.store.load_plan(operation_id)
        current = operation.current_step_id
        if current not in plan.pause_safe_points:
            raise RExecOpValidationError(
                f"pause only allowed at pause_safe steps, current={current!r}"
            )
        operation.metadata["pause_requested"] = True
        self._transition(
            operation,
            OperationState.PAUSED,
            reason="operator_pause",
            correlation_id=operation.correlation_id,
        )
        cursor = self._execution_cursor(operation)
        cursor["paused_at_step_id"] = current
        operation.metadata["execution_cursor"] = cursor
        self.store.save_operation(operation)
        return operation

    def resume(self, operation_id: str) -> Operation:
        operation = self.store.load_operation(operation_id)
        if operation.state != OperationState.PAUSED.value:
            raise RExecOpValidationError(f"resume requires paused operation, got {operation.state}")
        self.preflight_rollback_authority(operation)
        self._transition(
            operation,
            OperationState.RESUMING,
            reason="operator_resume",
            correlation_id=operation.correlation_id,
        )
        operation.metadata.pop("pause_requested", None)
        self.store.save_operation(operation)
        self._prepare_start(operation)
        return self._continue_execution(operation_id)

    def cancel(self, operation_id: str) -> Operation:
        operation = self.store.load_operation(operation_id)
        if operation.state not in {
            OperationState.WAITING_FOR_APPROVAL.value,
            OperationState.APPROVED.value,
            OperationState.RUNNING.value,
            OperationState.PAUSED.value,
        }:
            raise RExecOpValidationError(
                f"operation cannot be cancelled from state: {operation.state}"
            )
        self._transition(
            operation,
            OperationState.CANCELLED,
            reason="operator_cancel",
            correlation_id=operation.correlation_id,
        )
        self.store.save_operation(operation)
        return operation

    def retry(self, operation_id: str) -> Operation:
        operation = self.store.load_operation(operation_id)
        if operation.state != OperationState.FAILED.value:
            raise RExecOpValidationError(f"retry requires failed operation, got {operation.state}")
        plan = self.preflight_rollback_authority(operation)
        failure = dict(operation.metadata.get("last_failure") or {})
        error_class = str(failure.get("error_class") or "")
        if not self._error_retryable(plan, error_class=error_class):
            raise RExecOpValidationError(f"retry not allowed for error_class={error_class!r}")
        cursor = self._execution_cursor(operation)
        step_id = str(failure.get("step_id") or "")
        attempts_by_step = dict(cursor.get("attempts_by_step") or {})
        if step_id:
            attempts_by_step[step_id] = 0
        cursor["attempts_by_step"] = attempts_by_step
        operation.metadata["execution_cursor"] = cursor
        self._transition(
            operation,
            OperationState.RETRYING,
            reason="operator_retry",
            correlation_id=operation.correlation_id,
        )
        self.store.save_operation(operation)
        self._prepare_start(operation)
        return self._continue_execution(operation_id)

    def validate(self, operation_id: str) -> dict[str, Any]:
        operation = self.store.load_operation(operation_id)
        shared_state = dict(operation.metadata.get("shared_state") or {})
        return validate_operation_result(
            intent=operation.intent,
            shared_state=shared_state,
            profile=self._profile_for_operation(operation),
        )

    def escalate(self, operation_id: str) -> dict[str, Any]:
        operation = self.store.load_operation(operation_id)
        if operation.state not in {OperationState.FAILED.value, OperationState.BLOCKED.value}:
            raise RExecOpValidationError(f"operation not escalatable from state: {operation.state}")
        package = build_escalation_package(operation=operation, store=self.store)
        self._transition(
            operation,
            OperationState.ESCALATED,
            reason="operator_escalation",
            correlation_id=operation.correlation_id,
        )
        self._emit_simple_event(
            operation,
            EvidenceEventType.OPERATION_ESCALATED,
            correlation_id=operation.correlation_id,
            payload={"package": package},
        )
        operation.metadata["escalation_package"] = package
        self.store.save_operation(operation)
        return package

    def _prepare_start(self, operation: Operation) -> None:
        plan = self.preflight_rollback_authority(operation)
        if (
            self._is_rollback_operation(operation)
            and plan.required_connectors
            and self._governed_attempt_consumer is None
        ):
            raise RExecOpValidationError(
                "rollback connector execution requires governed approval authority"
            )
        self._policy_enforcement_for_operation(operation, plan)
        correlation_id = operation.correlation_id

        if operation.state == OperationState.PLANNED.value and operation.mode in READ_ONLY_MODES:
            self._transition(
                operation,
                OperationState.APPROVED,
                reason="read_only_auto_approved",
                correlation_id=correlation_id,
            )
        elif operation.state == OperationState.PLANNED.value and is_mutating_mode(operation.mode):
            raise RExecOpValidationError("mutating operation must be approved before start")

        if operation.state == OperationState.RESUMING.value:
            self._transition(
                operation,
                OperationState.RUNNING,
                reason="resume_execution",
                correlation_id=correlation_id,
            )
        elif operation.state == OperationState.RETRYING.value:
            self._transition(
                operation,
                OperationState.RUNNING,
                reason="retry_execution",
                correlation_id=correlation_id,
            )
        elif operation.state == OperationState.APPROVED.value:
            self._transition(
                operation,
                OperationState.RUNNING,
                reason="execution_started",
                correlation_id=correlation_id,
            )
            self._init_execution_cursor(operation, plan)

        if operation.state in {
            OperationState.BLOCKED.value,
            OperationState.WAITING_FOR_APPROVAL.value,
            OperationState.CANCELLED.value,
        }:
            raise RExecOpValidationError(
                f"operation cannot be started from state: {operation.state}"
            )

        if operation.state != OperationState.RUNNING.value:
            raise RExecOpStateError(f"operation must be running to execute, got {operation.state}")

        if is_mutating_mode(operation.mode) and not self._allows_mutating(operation):
            raise RExecOpValidationError(
                "mutating execution blocked until GovEngine allows and operation is approved"
            )

        self.store.save_operation(operation)

    def _continue_execution(
        self,
        operation_id: str,
        *,
        max_steps: int | None = None,
    ) -> Operation:
        steps_executed = 0
        while True:
            operation = self.store.load_operation(operation_id)
            if operation.state in TERMINAL_STATES or operation.state == OperationState.PAUSED.value:
                return operation

            if operation.state == OperationState.VALIDATING.value:
                return self._finalize_validation(operation)

            if operation.state != OperationState.RUNNING.value:
                return operation

            plan = self.store.load_plan(operation_id)
            cursor = self._execution_cursor(operation)
            start_index = int(cursor.get("next_step_index") or 0)
            if start_index >= len(plan.planned_steps):
                return self._begin_validation(operation)

            if operation.metadata.get("cancel_requested"):
                self._transition(
                    operation,
                    OperationState.CANCELLED,
                    reason="operator_cancel",
                    correlation_id=operation.correlation_id,
                )
                self.store.save_operation(operation)
                return operation

            shared_state = dict(operation.metadata.get("shared_state") or {})
            connectors = operation.metadata.get("environment_connectors")
            profile_root = str(operation.metadata.get("profile_root") or "").strip()
            if profile_root and isinstance(connectors, dict):
                shared_state["execution_context"] = {
                    "profile_root": profile_root,
                    "connectors": dict(connectors),
                }
                shared_state["typed_execution_governance"] = typed_execution_governance_overlay(
                    operation.as_dict()
                )
            sink = _WorkflowEvidenceSink(self, operation)
            policy_enforcement = self._policy_enforcement_for_operation(
                operation,
                plan,
            )
            run_result = self._runner_for_operation(operation).run(
                operation_id=operation.id,
                target=operation.target,
                mode=operation.mode,
                planned_steps=plan.planned_steps,
                correlation_id=operation.correlation_id,
                evidence_sink=sink,
                shared_state=shared_state,
                start_index=start_index,
                max_steps=1,
                policy_enforcement=policy_enforcement,
            )
            latest = self.store.load_operation(operation_id)
            latest.current_step_id = operation.current_step_id
            latest.evidence_event_ids = list(
                dict.fromkeys(latest.evidence_event_ids + operation.evidence_event_ids)
            )
            operation = latest
            operation.metadata["shared_state"] = run_result.shared_state
            operation.metadata["step_results"] = run_result.step_results
            cursor["next_step_index"] = run_result.next_step_index
            operation.metadata["execution_cursor"] = cursor
            self._record_typed_execution_spec_logs(
                operation,
                run_result.step_results,
                correlation_id=operation.correlation_id,
            )
            self.store.save_operation(operation)

            if not run_result.success:
                return self._handle_step_failure(operation, plan, run_result)

            last_step_id = run_result.executed_steps[-1] if run_result.executed_steps else ""
            operation.current_step_id = last_step_id
            steps_executed += 1

            if operation.metadata.get("pause_requested") and last_step_id in plan.pause_safe_points:
                self._transition(
                    operation,
                    OperationState.PAUSED,
                    reason="operator_pause",
                    correlation_id=operation.correlation_id,
                )
                cursor["paused_at_step_id"] = last_step_id
                operation.metadata["execution_cursor"] = cursor
                self.store.save_operation(operation)
                return operation

            if run_result.next_step_index >= len(plan.planned_steps):
                operation = self.store.load_operation(operation_id)
                return self._begin_validation(operation)

            if max_steps is not None and steps_executed >= max_steps:
                return self.store.load_operation(operation_id)

    def _policy_enforcement_for_operation(
        self,
        operation: Operation,
        plan: OperationPlan,
    ) -> dict[str, Any]:
        policy_raw = operation.metadata.get("policy_pack")
        if not isinstance(policy_raw, dict):
            return {}
        record = operation.metadata.get("policy_enforcement")
        verdict = operation.metadata.get("policy_verdict")
        if not isinstance(record, dict) or not isinstance(verdict, dict):
            raise RExecOpValidationError("policy enforcement binding is missing")
        policy_pack = compile_environment_policy_pack(policy_raw)
        if policy_pack is None:
            raise RExecOpValidationError("compiled policy pack is missing")
        connectors = operation.metadata.get("environment_connectors")
        enforcement_plan, admission = validate_policy_enforcement_record(
            record,
            policy_pack=policy_pack,
            verdict=verdict,
            planned_steps=plan.planned_steps,
            connectors=connectors if isinstance(connectors, dict) else {},
        )
        plan_digest = str(record.get("plan_digest") or "")
        admission_digest = str(record.get("admission_digest") or "")
        controls = enforcement_plan.controls.as_dict()
        if enforcement_plan.controls.max_output_bytes == 0:
            controls.pop("max_output_bytes", None)
        return {
            "binding": execution_policy_binding(
                enforcement_plan,
                admission,
                plan_digest=plan_digest,
                admission_digest=admission_digest,
            ),
            "controls": controls,
        }

    def _begin_validation(self, operation: Operation) -> Operation:
        self._transition(
            operation,
            OperationState.VALIDATING,
            reason="validation_started",
            correlation_id=operation.correlation_id,
        )
        self._emit_simple_event(
            operation,
            EvidenceEventType.VALIDATION_STARTED,
            correlation_id=operation.correlation_id,
        )
        self.store.save_operation(operation)
        return self._finalize_validation(operation)

    def _finalize_validation(self, operation: Operation) -> Operation:
        shared_state = dict(operation.metadata.get("shared_state") or {})
        if self._is_rollback_operation(operation):
            validation = self._validate_rollback_result(operation, shared_state)
        else:
            validation = validate_operation_result(
                intent=operation.intent,
                shared_state=shared_state,
                profile=self._profile_for_operation(operation),
            )
        operation.metadata["validation"] = validation
        self._emit_simple_event(
            operation,
            EvidenceEventType.VALIDATION_COMPLETED,
            correlation_id=operation.correlation_id,
            payload=validation,
        )

        if validation.get("passed"):
            self._transition(
                operation,
                OperationState.COMPLETED,
                reason="validation_passed",
                correlation_id=operation.correlation_id,
            )
            self._emit_simple_event(
                operation,
                EvidenceEventType.OPERATION_COMPLETED,
                correlation_id=operation.correlation_id,
            )
            self.store.save_operation(operation)
            self._maybe_plan_auto_reaction(operation)
        else:
            self._transition(
                operation,
                OperationState.FAILED,
                reason="validation_failed",
                correlation_id=operation.correlation_id,
            )
            self._emit_simple_event(
                operation,
                EvidenceEventType.OPERATION_FAILED,
                correlation_id=operation.correlation_id,
                payload=validation,
            )

        self.store.save_operation(operation)
        return operation

    def _validate_rollback_result(
        self,
        operation: Operation,
        shared_state: dict[str, Any],
    ) -> dict[str, Any]:
        plan = self.store.load_plan(operation.id)
        results_raw = shared_state.get("step_results")
        results = dict(results_raw) if isinstance(results_raw, dict) else {}
        missing_or_failed: list[str] = []
        receipt_failures: list[str] = []
        connector_receipts = 0
        for step in plan.planned_steps:
            step_id = str(step.get("id") or "")
            result = results.get(step_id)
            if not isinstance(result, dict) or result.get("success") is not True:
                missing_or_failed.append(step_id)
                continue
            if str(step.get("type") or "") != "connector":
                continue
            conformance = result.get("receipt_conformance")
            if not isinstance(conformance, dict) or conformance.get("conformant") is not True:
                receipt_failures.append(step_id)
            else:
                connector_receipts += 1
        passed = not missing_or_failed and not receipt_failures
        return {
            "passed": passed,
            "rule": (
                "rexecop.rollback_steps_completed"
                if passed
                else "rexecop.rollback_execution_incomplete"
            ),
            "details": {
                "parent_operation_id": str(
                    (operation.metadata.get("derived_operation") or {}).get("parent_operation_id")
                ),
                "planned_step_count": len(plan.planned_steps),
                "connector_receipt_count": connector_receipts,
                "missing_or_failed_steps": missing_or_failed,
                "receipt_failures": receipt_failures,
            },
        }

    def _maybe_plan_auto_reaction(self, operation: Operation) -> None:
        if self._auto_reaction_handler is None:
            return
        result = self._auto_reaction_handler(operation)
        if not result:
            return
        operation.metadata["auto_reaction"] = result
        event_id = self.evidence.emit(
            operation_id=operation.id,
            event_type=EvidenceEventType.REACTION_PLANNED,
            correlation_id=operation.correlation_id,
            state_before=operation.state,
            state_after=operation.state,
            payload=result,
        )
        operation.evidence_event_ids.append(event_id)

    def _handle_step_failure(
        self,
        operation: Operation,
        plan: OperationPlan,
        run_result: Any,
    ) -> Operation:
        step_id = run_result.stopped_step_id
        error_class = str(run_result.error_class or "unknown")
        cursor = self._execution_cursor(operation)
        attempts_by_step = dict(cursor.get("attempts_by_step") or {})
        attempts = int(attempts_by_step.get(step_id, 0)) + 1
        attempts_by_step[step_id] = attempts
        cursor["attempts_by_step"] = attempts_by_step
        operation.metadata["execution_cursor"] = cursor
        operation.metadata["last_failure"] = {
            "step_id": step_id,
            "error": run_result.error,
            "error_class": error_class,
            "attempt": attempts,
        }

        if self._can_retry(plan, error_class=error_class, attempts=attempts):
            self._transition(
                operation,
                OperationState.RETRYING,
                reason=f"retry_{error_class}",
                correlation_id=operation.correlation_id,
            )
            self.store.save_operation(operation)
            self._transition(
                operation,
                OperationState.RUNNING,
                reason="retry_execution",
                correlation_id=operation.correlation_id,
            )
            self.store.save_operation(operation)
            return self._continue_execution(operation.id)

        self._transition(
            operation,
            OperationState.FAILED,
            reason="step_execution_failed",
            correlation_id=operation.correlation_id,
        )
        self.store.save_operation(operation)
        return operation

    def _can_retry(
        self,
        plan: OperationPlan,
        *,
        error_class: str,
        attempts: int,
    ) -> bool:
        if not self._error_retryable(plan, error_class=error_class):
            return False
        max_attempts = int(plan.retry_policy_summary.get("max_attempts") or 0)
        return attempts <= max_attempts

    def _error_retryable(self, plan: OperationPlan, *, error_class: str) -> bool:
        if error_class in INTRINSICALLY_NON_RETRYABLE_ERRORS:
            return False
        policy = plan.retry_policy_summary
        blocked_on = [str(item) for item in policy.get("blocked_on") or []]
        allowed_on = [str(item) for item in policy.get("allowed_on") or []]
        if error_class in blocked_on:
            return False
        if allowed_on and error_class not in allowed_on:
            return False
        return bool(error_class)

    def _allows_mutating(self, operation: Operation) -> bool:
        if operation.govengine_decision_type in {item.value for item in BLOCKING_DECISIONS}:
            return False
        if operation.govengine_decision_type == "allowed":
            return True
        if operation.govengine_decision_type in {item.value for item in WAITING_DECISIONS}:
            try:
                self.store.load_approval(operation.id)
            except RExecOpValidationError:
                return False
            return True
        return False

    @staticmethod
    def _is_rollback_operation(operation: Operation) -> bool:
        derived = operation.metadata.get("derived_operation")
        return isinstance(derived, dict) and derived.get("kind") == "rollback"

    def _require_rollback_parent_authority(self, operation: Operation) -> None:
        if not self._is_rollback_operation(operation):
            return
        derived = operation.metadata.get("derived_operation")
        assert isinstance(derived, dict)
        parent_id = str(derived.get("parent_operation_id") or "")
        expected_digest = str(derived.get("failure_authority_digest") or "")
        if not parent_id or not expected_digest:
            raise RExecOpValidationError("rollback parent failure authority is missing")
        parent = self.store.load_operation(parent_id)
        if parent.state != OperationState.FAILED.value:
            raise RExecOpValidationError("rollback parent is no longer failed")
        parent_plan = self.store.load_plan(parent_id)
        current_digest = rollback_failure_authority_digest(parent, parent_plan)
        if not hmac.compare_digest(expected_digest, current_digest):
            raise RExecOpValidationError("rollback parent failure authority drift")

    def _require_rollback_catalog_binding(
        self,
        operation: Operation,
        plan: OperationPlan,
    ) -> None:
        if not self._is_rollback_operation(operation):
            return
        has_runtime_binding = isinstance(operation.metadata.get("catalog_runtime"), dict)
        if not has_runtime_binding and not plan.catalog_binding:
            return
        if self._catalog_binding_validator is None:
            raise RExecOpValidationError("rollback catalog binding validator is missing")
        self._catalog_binding_validator(operation, plan)

    def preflight_rollback_authority(self, operation: Operation) -> OperationPlan:
        """Validate persisted rollback authority without changing runtime state."""

        plan = self.store.load_plan(operation.id)
        self._require_rollback_catalog_binding(operation, plan)
        self._require_rollback_parent_authority(operation)
        return plan

    def _init_execution_cursor(self, operation: Operation, plan: OperationPlan) -> None:
        operation.metadata["execution_cursor"] = {
            "next_step_index": 0,
            "attempts_by_step": {},
            "paused_at_step_id": None,
            "pause_safe_points": list(plan.pause_safe_points),
        }

    def _profile_for_operation(self, operation: Operation) -> LoadedProfile | None:
        root = operation.metadata.get("profile_root")
        if not isinstance(root, str) or not root.strip():
            return None
        return load_profile(Path(root))

    def _execution_cursor(self, operation: Operation) -> dict[str, Any]:
        cursor = operation.metadata.get("execution_cursor")
        if not isinstance(cursor, dict):
            cursor = {"next_step_index": 0, "attempts_by_step": {}}
            operation.metadata["execution_cursor"] = cursor
        return cursor

    def _record_typed_execution_spec_logs(
        self,
        operation: Operation,
        step_results: dict[str, dict[str, Any]],
        *,
        correlation_id: str,
    ) -> None:
        if self.structured_log is None:
            return
        for step_id, result in step_results.items():
            if not isinstance(result, dict):
                continue
            output = result.get("output")
            if not isinstance(output, dict):
                continue
            admission = output.get("typed_execution_admission")
            if not isinstance(admission, dict):
                continue
            spec_ref = str(admission.get("request_digest") or step_id)
            self.structured_log.emit(
                event_kind="spec_admission_recorded",
                correlation_id=correlation_id,
                message=f"Typed execution admission recorded for {step_id}",
                refs=StructuredLogRefs(
                    operation_id=operation.id,
                    spec_ref=spec_ref,
                ),
                details={
                    "step_id": step_id,
                    "allowed": admission.get("allowed"),
                    "reason_code": admission.get("reason_code"),
                },
            )

    def _emit_step_event(
        self,
        operation: Operation,
        event_type: EvidenceEventType,
        *,
        step_id: str,
        correlation_id: str,
        payload: dict[str, Any],
    ) -> None:
        allowlist = self._resolve_step_projection_allowlist(operation, step_id)
        event_id = self.evidence.emit(
            operation_id=operation.id,
            event_type=event_type,
            correlation_id=correlation_id,
            state_before=operation.state,
            state_after=operation.state,
            step_id=step_id,
            payload=payload,
            public_projection_allowlist=allowlist,
        )
        operation.evidence_event_ids.append(event_id)

    def _resolve_step_projection_allowlist(
        self,
        operation: Operation,
        step_id: str,
    ) -> frozenset[str]:
        profile_root = str(operation.metadata.get("profile_root") or "").strip()
        if not profile_root:
            return frozenset()
        try:
            plan = self.store.load_plan(operation.id)
        except (OSError, RExecOpValidationError):
            return frozenset()
        step = next(
            (item for item in plan.planned_steps if str(item.get("id") or "") == step_id),
            None,
        )
        if not isinstance(step, dict):
            return frozenset()
        connector = str(step.get("connector") or "").strip()
        action = str(step.get("action") or "").strip()
        if not connector or not action:
            return frozenset()
        try:
            profile = load_profile(Path(profile_root))
        except (OSError, RExecOpValidationError):
            return frozenset()
        declared = resolve_public_projection_allowlist(
            profile=profile,
            connector=connector,
            action=action,
        )
        return declared

    def _emit_simple_event(
        self,
        operation: Operation,
        event_type: EvidenceEventType,
        *,
        correlation_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        event_id = self.evidence.emit(
            operation_id=operation.id,
            event_type=event_type,
            correlation_id=correlation_id,
            state_before=operation.state,
            state_after=operation.state,
            payload=payload or {},
        )
        operation.evidence_event_ids.append(event_id)
