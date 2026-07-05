from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sclite.integrity import artifact_descriptor

from rexecop.adapters.govengine_port.adapter import default_govengine_adapter
from rexecop.adapters.govengine_port.contracts import (
    BLOCKING_DECISIONS,
    WAITING_DECISIONS,
    GovEngineAdapter,
    GovEngineDecision,
    GovEngineDecisionType,
    GovEngineRequest,
    is_mutating_mode,
)
from rexecop.adapters.sclite_port.contracts import SCLITE_ARTIFACT_AUTHORITY
from rexecop.catalog.service import CatalogService
from rexecop.connectors.action_shape import validate_http_action_shape
from rexecop.environment.loader import load_environment
from rexecop.environment.sanitize import sanitize_connectors_for_storage, validate_no_inline_secrets
from rexecop.environment.targets import validate_operation_target
from rexecop.errors import RExecOpValidationError
from rexecop.evidence.event import EvidenceEventType
from rexecop.observability.emitter import StructuredLogEmitter
from rexecop.observability.evidence import ObservabilityEvidenceManager
from rexecop.observability.structured_log import StructuredLogRefs
from rexecop.operation.model import Operation, StateTransitionRecord, utc_now_iso
from rexecop.operation.plan import OperationPlan
from rexecop.operation.state import OperationState, validate_transition
from rexecop.orchestration.orchestrator import OperationOrchestrator
from rexecop.policy.criticality import target_criticality
from rexecop.policy.enforcement import build_policy_enforcement_record
from rexecop.policy.operation import evaluate_operation_policy, require_operation_policy_allows_plan
from rexecop.policy.pack import compile_environment_policy_pack, policy_decision_from_verdict
from rexecop.profile.loader import load_profile
from rexecop.profile.resolver import resolve_profile_path
from rexecop.reaction.model import ReactionContext
from rexecop.runtime_ops.idempotency import (
    attach_operation_idempotency,
    plan_idempotency_key,
    start_idempotency_key,
)
from rexecop.runtime_ops.recovery import ensure_terminal_receipt, start_is_idempotent
from rexecop.storage.atomic import atomic_write_text
from rexecop.storage.factory import create_store
from rexecop.storage.port import RuntimeStore
from rexecop.workflow.contract import validate_workflow_contract
from rexecop.workflow.loader import load_workflow

DEFAULT_MODE = "dry_run"
SUPPORTED_MODES = frozenset(
    {"observe", "dry_run", "apply", "emergency_readonly", "recovery"}
)


def generate_operation_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    short_id = secrets.token_hex(3)
    return f"op-{stamp}-{short_id}"


class OperationController:
    def __init__(
        self,
        store: RuntimeStore | None = None,
        govengine_adapter: GovEngineAdapter | None = None,
    ) -> None:
        self.store = store or create_store()
        self.structured_log = StructuredLogEmitter(self.store)
        self.evidence = ObservabilityEvidenceManager(self.store, self.structured_log)
        self.govengine_adapter = govengine_adapter or default_govengine_adapter()
        from rexecop.adapters.sclite_port.emitter import SCLiteArtifactEmitter
        from rexecop.runtime_ops.coordinator import RuntimeCoordinator
        from rexecop.runtime_ops.rollback import RollbackExecutor

        self.sclite_emitter = SCLiteArtifactEmitter()
        self.runtime = RuntimeCoordinator(self.store)
        self.rollback_executor = RollbackExecutor()
        self.orchestrator = OperationOrchestrator(
            store=self.store,
            evidence=self.evidence,
            structured_log=self.structured_log,
            transition=self._transition,
            export_receipt=self.export_receipt,
            auto_reaction_handler=self._maybe_plan_auto_reaction,
        )

    def plan(
        self,
        *,
        profile_path: str | Path | None = None,
        environment_path: Path | None = None,
        intent: str,
        target: str,
        mode: str = DEFAULT_MODE,
        requested_by: str = "operator",
        catalog_path: Path | None = None,
        auto_react: str | None = None,
    ) -> Operation:
        if mode not in SUPPORTED_MODES:
            raise RExecOpValidationError(f"unsupported mode: {mode}")
        auto_react_mode = (auto_react or "").strip()
        if auto_react_mode and auto_react_mode != "plan_only":
            raise RExecOpValidationError(
                f"unsupported auto_react mode: {auto_react_mode}"
            )

        catalog_resolution = None
        resolved_catalog_path: Path | None = None
        if catalog_path is not None:
            resolved_catalog_path = catalog_path.expanduser().resolve()
            catalog_resolution = CatalogService(resolved_catalog_path).resolve_operation(
                target, intent
            )
            if not catalog_resolution.applicability.applicable:
                raise RExecOpValidationError(
                    "catalog operation is not applicable: "
                    f"{catalog_resolution.applicability.status}"
                )
            if profile_path is not None:
                supplied_profile = resolve_profile_path(profile_path).resolve()
                if supplied_profile.is_file():
                    supplied_profile = supplied_profile.parent
                if supplied_profile != catalog_resolution.target.profile_path.resolve():
                    raise RExecOpValidationError(
                        "catalog profile does not match supplied profile"
                    )
            if environment_path is not None and (
                environment_path.expanduser().resolve()
                != catalog_resolution.target.environment_path.resolve()
            ):
                raise RExecOpValidationError(
                    "catalog environment does not match supplied environment"
                )
            profile_path = catalog_resolution.target.profile_path
            environment_path = catalog_resolution.target.environment_path
            target = catalog_resolution.target.environment_target

        if profile_path is None:
            raise RExecOpValidationError("profile is required without a target catalog")
        if environment_path is None:
            raise RExecOpValidationError("environment is required without a target catalog")

        resolved_profile_path = resolve_profile_path(profile_path)
        profile = load_profile(resolved_profile_path)
        environment = load_environment(environment_path)
        validate_no_inline_secrets(environment.as_dict())
        if environment.profile and environment.profile != profile.name:
            raise RExecOpValidationError(
                f"environment profile {environment.profile} does not match {profile.name}"
            )

        intent_meta = profile.intent_metadata(intent)
        intent_modes = intent_meta.get("modes")
        if intent_meta.get("enforce_declared_modes") is True and (
            not isinstance(intent_modes, list) or mode not in intent_modes
        ):
            raise RExecOpValidationError(
                f"mode {mode} not declared for intent: {intent}"
            )
        workflow_path = profile.resolve_workflow_path(intent)
        workflow = load_workflow(workflow_path)
        validate_operation_target(environment, target)
        validate_workflow_contract(workflow, environment, profile)
        compiled_policy = compile_environment_policy_pack(environment.policy_pack)
        target_crit = target_criticality(environment, target)

        operation_id = generate_operation_id()
        correlation_id = str(uuid.uuid4())
        now = utc_now_iso()

        operation = Operation(
            id=operation_id,
            profile=profile.name,
            environment=environment.id,
            intent=intent,
            target=target,
            mode=mode,
            requested_by=requested_by,
            state=OperationState.CREATED.value,
            created_at=now,
            updated_at=now,
            correlation_id=correlation_id,
        )

        operation.metadata["profile_root"] = str(profile.root)
        operation.metadata["environment_path"] = str(environment_path.expanduser().resolve())
        operation.metadata["environment_connectors"] = sanitize_connectors_for_storage(
            environment.connectors
        )
        if auto_react_mode:
            operation.metadata["auto_react"] = {
                "mode": auto_react_mode,
                "depth": 0,
                "reaction_count": 0,
                "visited_rule_digests": [],
            }
        http_action_bindings: dict[str, str] = {}
        for step in workflow.steps:
            if step.type != "connector" or not step.connector:
                continue
            config = environment.connectors.get(step.connector)
            contract = profile.connector_contract(step.connector)
            if not isinstance(config, dict) or not isinstance(contract, dict):
                continue
            if str(config.get("backend") or config.get("mode") or "") != "http_api":
                continue
            digest = validate_http_action_shape(
                connector_name=step.connector,
                action=step.action,
                connector_contract=contract,
                connector_config=config,
            )
            if digest:
                http_action_bindings[f"{step.connector}.{step.action}"] = digest
        if http_action_bindings:
            operation.metadata["http_action_bindings"] = http_action_bindings
        operation.metadata["runtime_policy"] = {
            "max_concurrent_operations": int(
                environment.safety.get("max_concurrent_operations") or 1
            ),
            "target_lock_enabled": bool(
                environment.safety.get("target_lock_enabled", True)
            ),
            "maintenance_windows": list(
                environment.safety.get("maintenance_windows") or []
            ),
        }
        if environment.policy_pack:
            operation.metadata["policy_pack"] = dict(environment.policy_pack)
        operation.metadata["target_criticality"] = target_crit
        if catalog_resolution is not None:
            assert resolved_catalog_path is not None
            operation.metadata["catalog_binding"] = catalog_resolution.binding.as_dict()
            operation.metadata["catalog_runtime"] = {
                "catalog_path": str(resolved_catalog_path),
                "target_id": catalog_resolution.target.id,
            }

        attach_operation_idempotency(
            operation.metadata,
            plan_key=plan_idempotency_key(
                profile=profile.name,
                environment=environment.id,
                intent=intent,
                target=target,
                mode=mode,
                catalog_binding=(
                    catalog_resolution.binding.as_dict() if catalog_resolution is not None else None
                ),
                auto_react=auto_react_mode or None,
            ),
            start_key=start_idempotency_key(operation.id),
        )

        govengine_preview: dict[str, Any] = {
            "profile": profile.name,
            "environment": environment.id,
            "intent": intent,
            "target": target,
            "mode": mode,
            "risk": str(intent_meta.get("risk") or workflow.risk),
            "note": "preview only; not a governance decision",
        }
        if compiled_policy is not None:
            verdict = evaluate_operation_policy(
                policy_pack=compiled_policy,
                operation_id=operation.id,
                profile=profile.name,
                environment=environment,
                intent=intent,
                target=target,
                mode=mode,
                risk=str(intent_meta.get("risk") or workflow.risk),
            )
            govengine_preview["policy_decision"] = policy_decision_from_verdict(verdict)
            operation.metadata["policy_verdict"] = verdict.as_dict()
            operation.metadata["policy_enforcement"] = build_policy_enforcement_record(
                compiled_policy,
                verdict,
            )
            require_operation_policy_allows_plan(verdict, controls_enforced=True)

        created_event = self.evidence.emit(
            operation_id=operation.id,
            event_type=EvidenceEventType.OPERATION_CREATED,
            correlation_id=correlation_id,
            state_after=operation.state,
            payload={
                "profile": profile.name,
                "environment": environment.id,
                "intent": intent,
                "target": target,
                "mode": mode,
            },
        )
        operation.evidence_event_ids.append(created_event)

        plan = OperationPlan(
            operation_id=operation.id,
            profile=profile.name,
            environment=environment.id,
            intent=intent,
            target=target,
            mode=mode,
            workflow=workflow.as_dict(),
            planned_steps=[step.as_dict() for step in workflow.steps],
            required_connectors=workflow.required_connectors(),
            risk=str(intent_meta.get("risk") or workflow.risk),
            govengine_request_preview=govengine_preview,
            expected_evidence=[
                "plan_generated",
                "state_transition",
            ],
            pause_safe_points=workflow.pause_safe_points(),
            retry_policy_summary=dict(workflow.retry or {"max_attempts": 0}),
            rollback_available=bool(workflow.rollback),
            catalog_binding=(
                catalog_resolution.binding.as_dict()
                if catalog_resolution is not None
                else {}
            ),
        )

        self._transition(
            operation,
            OperationState.PLANNED,
            reason="plan_completed",
            correlation_id=correlation_id,
        )

        plan_event = self.evidence.emit(
            operation_id=operation.id,
            event_type=EvidenceEventType.PLAN_GENERATED,
            correlation_id=correlation_id,
            state_before=OperationState.CREATED.value,
            state_after=operation.state,
            payload={
                "planned_steps": plan.planned_steps,
                "workflow_id": workflow.id,
                "catalog_binding": dict(plan.catalog_binding),
            },
        )
        operation.evidence_event_ids.append(plan_event)

        self.store.save_plan(plan)
        self.structured_log.emit(
            event_kind="plan_recorded",
            correlation_id=correlation_id,
            message="Operation plan recorded",
            refs=StructuredLogRefs(
                operation_id=operation.id,
                plan_id=plan.operation_id,
                evidence_ref=plan_event,
            ),
            details={
                "intent": intent,
                "mode": mode,
                "planned_steps": len(plan.planned_steps),
            },
        )

        if is_mutating_mode(mode):
            decision = self._evaluate_governance(operation, plan, correlation_id)
            self._apply_governance_transition(operation, decision, correlation_id)

        self._emit_sclite_intent(operation, plan)

        self.store.save_operation(operation)
        return operation

    def _maybe_plan_auto_reaction(self, operation: Operation) -> dict[str, Any] | None:
        config = operation.metadata.get("auto_react")
        if not isinstance(config, dict):
            return None
        mode = str(config.get("mode") or "")
        if mode != "plan_only":
            raise RExecOpValidationError(f"unsupported auto_react mode: {mode}")
        if operation.mode not in {"observe", "dry_run", "emergency_readonly"}:
            raise RExecOpValidationError("auto_react supports read-only modes only")
        shared_state = operation.metadata.get("shared_state")
        if not isinstance(shared_state, dict) or not isinstance(
            shared_state.get("reaction_observation"),
            dict,
        ):
            return {
                "mode": mode,
                "status": "skipped",
                "reason": "reaction_observation_not_present",
            }
        profile_root = str(operation.metadata.get("profile_root") or "").strip()
        environment_path = str(operation.metadata.get("environment_path") or "").strip()
        if not profile_root or not environment_path:
            raise RExecOpValidationError("auto_react runtime binding is incomplete")

        from rexecop.reaction.service import ReactionService

        result = ReactionService(self).plan(
            profile_path=profile_root,
            environment_path=Path(environment_path),
            source_operation_id=operation.id,
            target=operation.target,
            mode=operation.mode,
            context=ReactionContext(
                depth=int(config.get("depth") or 0),
                reaction_count=int(config.get("reaction_count") or 0),
                visited_rule_digests=tuple(
                    str(item) for item in config.get("visited_rule_digests") or []
                ),
            ),
        )
        plan = result["reaction_plan"]
        rule_ref = plan.get("rule_ref")
        if not isinstance(rule_ref, dict):
            rule_ref = {}
        return {
            "mode": mode,
            "status": "planned",
            "reaction_id": result["reaction_id"],
            "chain_root": result["chain_root"],
            "idempotent_replay": bool(result.get("idempotent_replay")),
            "outcome": plan.get("outcome"),
            "reason": plan.get("reason"),
            "rule_id": rule_ref.get("id"),
            "rule_digest": rule_ref.get("digest"),
            "child_operation_id": plan.get("child_operation_id"),
            "admission": dict(plan.get("admission") or {}),
            "automation_admission": dict(result.get("automation_admission") or {}),
            "automation_chain_digest": str(
                (result.get("automation_admission") or {}).get("automation_chain_digest") or ""
            ),
        }

    def evaluate_governance(self, operation_id: str) -> GovEngineDecision:
        operation = self.get_operation(operation_id)
        plan = self.store.load_plan(operation_id)
        return self._evaluate_governance(operation, plan, operation.correlation_id)

    def allows_mutating_execution(self, operation_id: str) -> bool:
        operation = self.get_operation(operation_id)
        if not is_mutating_mode(operation.mode):
            return False
        if operation.govengine_decision_type in {
            item.value for item in BLOCKING_DECISIONS
        }:
            return False
        if operation.govengine_decision_type == GovEngineDecisionType.ALLOWED.value:
            return operation.state == OperationState.APPROVED.value
        if operation.govengine_decision_type in {item.value for item in WAITING_DECISIONS}:
            if operation.state != OperationState.APPROVED.value:
                return False
            approval_path = self.store.root / "approvals" / f"{operation_id}.json"
            return approval_path.is_file()
        return False

    def _governance_allows_rollback(self, operation: Operation) -> bool:
        if operation.govengine_decision_type == GovEngineDecisionType.ALLOWED.value:
            return True
        if operation.metadata.get("manual_approval"):
            return True
        return False

    def get_operation(self, operation_id: str) -> Operation:
        return self.store.load_operation(operation_id)

    def export_placeholder_receipt(self, operation_id: str) -> dict[str, object]:
        from rexecop.examples.bootstrap_receipt import export_placeholder_receipt_with_warning

        return export_placeholder_receipt_with_warning(self, operation_id)

    def export_receipt(self, operation_id: str) -> dict[str, object]:
        operation = self.get_operation(operation_id)
        plan = self.store.load_plan(operation_id)
        bundle_dir = self.store.operation_sclite_dir(operation_id)
        evidence_events = self.store.list_evidence_events(operation_id)
        emission = self.sclite_emitter.emit_operation_bundle(
            operation=operation,
            plan=plan,
            bundle_dir=str(bundle_dir),
            evidence_events=evidence_events,
        )
        operation.sclite_refs = emission.sclite_refs
        export_summary = emission.as_dict()
        path = self.store.save_receipt_export(operation_id, export_summary)
        receipt_event = self.evidence.emit(
            operation_id=operation_id,
            event_type=EvidenceEventType.RECEIPT_GENERATED,
            correlation_id=operation.correlation_id,
            state_before=operation.state,
            state_after=operation.state,
            payload={
                "authority": SCLITE_ARTIFACT_AUTHORITY,
                "emitter": "sclite",
                "bundle_dir": emission.bundle_dir,
                "receipt_export_path": str(path),
            },
        )
        operation.evidence_event_ids.append(receipt_event)
        self.structured_log.emit(
            event_kind="receipt_exported",
            correlation_id=operation.correlation_id,
            message="Receipt export recorded",
            refs=StructuredLogRefs(
                operation_id=operation_id,
                receipt_ref=str(path),
                evidence_ref=receipt_event,
            ),
            details={
                "authority": SCLITE_ARTIFACT_AUTHORITY,
                "review_verdict": emission.review_record.get("verdict"),
            },
        )
        self.store.save_operation(operation)
        return {
            "export": export_summary,
            "bundle_dir": emission.bundle_dir,
            "path": str(path),
            "sclite_refs": operation.sclite_refs,
            "review_verdict": emission.review_record.get("verdict"),
        }

    def get_history(self, operation_id: str) -> dict[str, object]:
        operation = self.get_operation(operation_id)
        evidence = self.store.list_evidence_events(operation_id)
        return {
            "operation_id": operation.id,
            "state": operation.state,
            "govengine_decision_type": operation.govengine_decision_type,
            "govengine_decision_summary": operation.govengine_decision_summary,
            "transitions": [item.as_dict() for item in operation.history],
            "evidence_events": evidence,
        }

    def start(self, operation_id: str) -> Operation:
        return self._start_operation(operation_id, drain_queue=True)

    def process_queue(self) -> list[str]:
        return self._drain_queue()

    def rollback(self, operation_id: str) -> dict[str, object]:
        operation = self.get_operation(operation_id)
        plan = self.store.load_plan(operation_id)
        result = self.rollback_executor.execute(
            operation=operation,
            plan=plan,
            govengine_allows=self._governance_allows_rollback(operation),
        )
        operation.metadata["rollback"] = result
        self.store.save_operation(operation)
        return result

    def _start_operation(self, operation_id: str, *, drain_queue: bool) -> Operation:
        operation = self.get_operation(operation_id)
        if start_is_idempotent(operation):
            return operation
        plan = self.store.load_plan(operation_id)
        self._verify_catalog_binding(operation, plan)
        if operation.state == OperationState.APPROVED.value and is_mutating_mode(operation.mode):
            self.runtime.check_maintenance_window(operation)
            if self.runtime.admit_for_execution(operation) == "queued":
                return self.get_operation(operation_id)
        result = self.orchestrator.start(operation_id)
        self._release_runtime_if_terminal(result)
        self._ensure_terminal_receipt_if_needed(operation_id)
        if drain_queue:
            self._drain_queue()
        return self.get_operation(operation_id)

    def _verify_catalog_binding(
        self,
        operation: Operation,
        plan: OperationPlan,
    ) -> None:
        runtime = operation.metadata.get("catalog_runtime")
        if not isinstance(runtime, dict):
            return
        catalog_path = str(runtime.get("catalog_path") or "").strip()
        target_id = str(runtime.get("target_id") or "").strip()
        if not catalog_path or not target_id or not plan.catalog_binding:
            raise RExecOpValidationError("catalog runtime binding is incomplete")
        current = CatalogService(Path(catalog_path)).resolve_operation(
            target_id,
            operation.intent,
        )
        if not current.applicability.applicable:
            raise RExecOpValidationError(
                f"catalog operation is no longer applicable: {current.applicability.status}"
            )
        if current.binding.as_dict() != plan.catalog_binding:
            raise RExecOpValidationError(
                "catalog binding drift detected; create a new operation plan"
            )

    def _release_runtime_if_terminal(self, operation: Operation) -> None:
        if operation.state in {
            OperationState.COMPLETED.value,
            OperationState.FAILED.value,
            OperationState.CANCELLED.value,
            OperationState.ESCALATED.value,
        }:
            self.runtime.release_operation(operation)

    def _ensure_terminal_receipt_if_needed(self, operation_id: str) -> None:
        ensure_terminal_receipt(self, operation_id)

    def _drain_queue(self) -> list[str]:
        started: list[str] = []
        while True:
            next_id = self.runtime.queue.peek()
            if not next_id:
                break
            candidate = self.get_operation(next_id)
            if candidate.state != OperationState.APPROVED.value:
                self.runtime.queue.remove(next_id)
                continue
            if self.runtime.admit_for_execution(candidate) != "admitted":
                break
            self._start_operation(next_id, drain_queue=False)
            started.append(next_id)
        return started

    def advance(self, operation_id: str, *, max_steps: int = 1) -> Operation:
        operation = self.get_operation(operation_id)
        if operation.state == OperationState.APPROVED.value and is_mutating_mode(operation.mode):
            self.runtime.check_maintenance_window(operation)
            if self.runtime.admit_for_execution(operation) == "queued":
                return self.get_operation(operation_id)
        result = self.orchestrator.advance(operation_id, max_steps=max_steps)
        self._release_runtime_if_terminal(result)
        self._ensure_terminal_receipt_if_needed(operation_id)
        return self.get_operation(operation_id)

    def approve(self, operation_id: str, *, approved_by: str = "operator") -> Operation:
        operation = self.get_operation(operation_id)
        if operation.state != OperationState.WAITING_FOR_APPROVAL.value:
            raise RExecOpValidationError(
                f"approve requires waiting_for_approval, got {operation.state}"
            )
        approval = {
            "operation_id": operation_id,
            "approved_by": approved_by,
            "approved_at": utc_now_iso(),
            "govengine_decision_type": operation.govengine_decision_type,
        }
        self.store.save_approval(operation_id, approval)
        operation.metadata["manual_approval"] = approval
        approval_event = self.evidence.emit(
            operation_id=operation_id,
            event_type=EvidenceEventType.APPROVAL_RECEIVED,
            correlation_id=operation.correlation_id,
            state_before=operation.state,
            state_after=OperationState.APPROVED.value,
            payload=approval,
        )
        operation.evidence_event_ids.append(approval_event)
        self._transition(
            operation,
            OperationState.APPROVED,
            reason="manual_approval",
            correlation_id=operation.correlation_id,
        )
        self.store.save_operation(operation)
        return operation

    def pause(self, operation_id: str) -> Operation:
        return self.orchestrator.pause(operation_id)

    def resume(self, operation_id: str) -> Operation:
        operation = self.get_operation(operation_id)
        if is_mutating_mode(operation.mode):
            self.runtime.check_maintenance_window(operation)
            if self.runtime.admit_for_execution(operation) == "queued":
                return self.get_operation(operation_id)
        result = self.orchestrator.resume(operation_id)
        self._release_runtime_if_terminal(result)
        self._ensure_terminal_receipt_if_needed(operation_id)
        self._drain_queue()
        return self.get_operation(operation_id)

    def cancel(self, operation_id: str) -> Operation:
        operation = self.get_operation(operation_id)
        result = self.orchestrator.cancel(operation_id)
        self.runtime.release_operation(operation)
        self.runtime.queue.remove(operation_id)
        self._drain_queue()
        return result

    def retry(self, operation_id: str) -> Operation:
        operation = self.get_operation(operation_id)
        if is_mutating_mode(operation.mode):
            self.runtime.check_maintenance_window(operation)
            if self.runtime.admit_for_execution(operation) == "queued":
                return self.get_operation(operation_id)
        result = self.orchestrator.retry(operation_id)
        self._release_runtime_if_terminal(result)
        self._ensure_terminal_receipt_if_needed(operation_id)
        self._drain_queue()
        return self.get_operation(operation_id)

    def validate(self, operation_id: str) -> dict[str, object]:
        return self.orchestrator.validate(operation_id)

    def escalate(self, operation_id: str) -> dict[str, object]:
        package = self.orchestrator.escalate(operation_id)
        self._ensure_terminal_receipt_if_needed(operation_id)
        return package

    def _emit_sclite_intent(self, operation: Operation, plan: OperationPlan) -> None:
        intent = self.sclite_emitter.emit_intent_contract(operation, plan)
        bundle_dir = self.store.operation_sclite_dir(operation.id)
        intent_path = bundle_dir / "01_intent_contract.json"
        atomic_write_text(
            intent_path,
            json.dumps(intent, indent=2, sort_keys=True) + "\n",
        )
        descriptor = artifact_descriptor(intent)
        operation.sclite_refs = {
            **operation.sclite_refs,
            "intent_contract": {
                "sclite_schema_ref": descriptor["schema_ref"],
                "descriptor_path": str(intent_path),
                "digest": descriptor["digest"],
                "status": "emitted",
            },
        }

    def _evaluate_governance(
        self,
        operation: Operation,
        plan: OperationPlan,
        correlation_id: str,
    ) -> GovEngineDecision:
        request = GovEngineRequest(
            operation_id=operation.id,
            profile=plan.profile,
            environment=plan.environment,
            intent=plan.intent,
            target=plan.target,
            mode=plan.mode,
            risk=plan.risk,
            preview=plan.govengine_request_preview,
        )

        requested_event = self.evidence.emit(
            operation_id=operation.id,
            event_type=EvidenceEventType.GOVENGINE_DECISION_REQUESTED,
            correlation_id=correlation_id,
            state_before=operation.state,
            payload=request.as_dict(),
        )
        operation.evidence_event_ids.append(requested_event)

        decision = self.govengine_adapter.evaluate(request)
        operation.govengine_decision_type = decision.decision_type.value
        operation.govengine_decision_summary = decision.summary
        operation.metadata["govengine_admission"] = decision.as_dict()

        received_event = self.evidence.emit(
            operation_id=operation.id,
            event_type=EvidenceEventType.GOVENGINE_DECISION_RECEIVED,
            correlation_id=correlation_id,
            state_before=operation.state,
            payload=decision.as_dict(),
        )
        operation.evidence_event_ids.append(received_event)
        admission_ref = str(
            decision.as_dict().get("request_digest")
            or decision.as_dict().get("decision_type")
            or operation.id
        )
        self.structured_log.emit(
            event_kind="admission_decided",
            correlation_id=correlation_id,
            message=f"GovEngine admission decision: {decision.decision_type.value}",
            refs=StructuredLogRefs(
                operation_id=operation.id,
                plan_id=plan.operation_id,
                admission_ref=admission_ref,
                evidence_ref=received_event,
            ),
            details={
                "decision_type": decision.decision_type.value,
                "summary": decision.summary,
            },
        )
        return decision

    def _apply_governance_transition(
        self,
        operation: Operation,
        decision: GovEngineDecision,
        correlation_id: str,
    ) -> None:
        if decision.decision_type == GovEngineDecisionType.ALLOWED:
            self._transition(
                operation,
                OperationState.APPROVED,
                reason="govengine_allowed",
                correlation_id=correlation_id,
            )
            return

        if decision.decision_type in WAITING_DECISIONS:
            self._transition(
                operation,
                OperationState.WAITING_FOR_APPROVAL,
                reason=f"govengine_{decision.decision_type.value}",
                correlation_id=correlation_id,
            )
            return

        if decision.decision_type in BLOCKING_DECISIONS:
            self._transition(
                operation,
                OperationState.BLOCKED,
                reason=f"govengine_{decision.decision_type.value}",
                correlation_id=correlation_id,
            )
            return

        self._transition(
            operation,
            OperationState.BLOCKED,
            reason="govengine_unknown_decision",
            correlation_id=correlation_id,
        )

    def _transition(
        self,
        operation: Operation,
        target: OperationState,
        *,
        reason: str,
        correlation_id: str,
    ) -> None:
        current = operation.operation_state
        validate_transition(current, target)
        record = StateTransitionRecord(
            from_state=current.value,
            to_state=target.value,
            timestamp_utc=utc_now_iso(),
            reason=reason,
        )
        operation.history.append(record)
        operation.state = target.value
        operation.updated_at = record.timestamp_utc

        event_id = self.evidence.emit(
            operation_id=operation.id,
            event_type=EvidenceEventType.STATE_TRANSITION,
            correlation_id=correlation_id,
            state_before=current.value,
            state_after=target.value,
            payload={"reason": reason},
        )
        operation.evidence_event_ids.append(event_id)
