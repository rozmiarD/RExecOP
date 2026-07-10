from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sclite import (
    automation_edge,
    automation_node,
    build_automation_chain,
    build_finding,
    build_reaction_chain_manifest,
    build_reaction_plan,
    reaction_idempotency_key,
    validate_escalation_proposal,
    verify_automation_chain,
    verify_reaction_chain_manifest,
)
from sclite.artifacts import artifact_sha256, canonical_artifact_bytes, validate_artifact

from rexecop.environment.loader import load_environment
from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.policy.operation import evaluate_operation_policy
from rexecop.policy.pack import compile_environment_policy_pack
from rexecop.profile.loader import LoadedProfile, load_profile
from rexecop.profile.resolver import resolve_profile_path
from rexecop.reaction.automation_admission import (
    AUTOMATION_CHAIN_SCHEMA_REF,
    admit_automation_transition_request,
    automation_transition_contract_available,
    unavailable_automation_binding,
)
from rexecop.reaction.automation_graph import verify_runtime_automation_graph
from rexecop.reaction.compiler import compile_reaction_pack
from rexecop.reaction.evaluator import evaluate_reaction
from rexecop.reaction.model import ReactionContext
from rexecop.runtime_ops.idempotency import reaction_child_plan_key
from rexecop.storage.atomic import atomic_write_text, secure_directory

MAX_OBSERVATION_BYTES = 1_048_576


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RExecOpValidationError(f"JSON artifact not found: {path}")
    if path.stat().st_size > MAX_OBSERVATION_BYTES:
        raise RExecOpValidationError(f"JSON artifact exceeds {MAX_OBSERVATION_BYTES} bytes")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RExecOpValidationError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise RExecOpValidationError(f"JSON artifact must be an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_write_text(path, json.dumps(dict(value), indent=2, sort_keys=True) + "\n")


def _plain_allow(verdict: Any) -> bool:
    return (
        verdict.decision == "allow"
        and not verdict.obligations
        and not verdict.constraints
        and not verdict.blockers
    )


def _validate_reaction_intent(profile: LoadedProfile, intent_ref: str) -> None:
    metadata = profile.intent_metadata(intent_ref)
    modes = metadata.get("modes")
    if not isinstance(modes, list) or not modes:
        raise RExecOpValidationError(f"proposal intent must declare modes: {intent_ref}")
    if any(
        str(mode) not in {"observe", "dry_run", "emergency_readonly", "read_only"} for mode in modes
    ):
        raise RExecOpValidationError(f"proposal intent is not read-only: {intent_ref}")


class ReactionService:
    def __init__(self, controller: OperationController | None = None) -> None:
        self.controller = controller or OperationController()
        self.root = self.controller.store.root / "reactions"

    def plan(
        self,
        *,
        profile_path: str | Path,
        environment_path: Path,
        observation_path: Path | None = None,
        source_operation_id: str | None = None,
        target: str,
        mode: str = "dry_run",
        context: ReactionContext | None = None,
    ) -> dict[str, Any]:
        if mode not in {"observe", "dry_run", "emergency_readonly"}:
            raise RExecOpValidationError("reaction planning supports read-only modes only")
        if (observation_path is None) == (source_operation_id is None):
            raise RExecOpValidationError(
                "exactly one of observation_path or source_operation_id is required"
            )
        profile = load_profile(resolve_profile_path(profile_path))
        pack = compile_reaction_pack(profile)
        catalog_runtime: tuple[Path, str] | None = None
        if source_operation_id is not None:
            observation = self._observation_from_operation(source_operation_id)
            catalog_runtime = self._catalog_runtime_from_operation(source_operation_id)
        else:
            assert observation_path is not None
            observation = _read_json(observation_path)
        validate_artifact(observation, "schemas/observation_envelope.v0.1.schema.json")
        if len(canonical_artifact_bytes(observation)) > MAX_OBSERVATION_BYTES:
            raise RExecOpValidationError("canonical observation exceeds bounded size")
        profile_ref = observation.get("profile_ref")
        if not isinstance(profile_ref, Mapping) or str(profile_ref.get("id")) != profile.name:
            raise RExecOpValidationError("observation profile does not match selected profile")
        if str(profile_ref.get("version")) != profile.version:
            raise RExecOpValidationError(
                "observation profile version does not match selected profile"
            )
        if str(profile_ref.get("digest")) != pack.profile_digest:
            raise RExecOpValidationError(
                "observation profile digest does not match reaction snapshot"
            )
        source = observation.get("source")
        if not isinstance(source, Mapping) or str(source.get("target_id")) != target:
            raise RExecOpValidationError("observation target does not match requested target")

        reaction_context = context or ReactionContext()
        evaluation = evaluate_reaction(pack, observation, reaction_context)
        key = reaction_idempotency_key(
            profile_digest=pack.profile_digest,
            observation=observation,
            rule_digest=evaluation.rule.digest,
            target_id=target,
        )
        reaction_id = f"reaction-{key[:24]}"
        directory = self.root / reaction_id
        if directory.exists():
            existing_plan = _read_json(directory / "03_reaction_plan.json")
            existing_context = existing_plan.get("context")
            if (
                not isinstance(existing_context, Mapping)
                or existing_context.get("idempotency_key") != key
            ):
                raise RExecOpValidationError("reaction id collision or idempotency drift")
            manifest = _read_json(directory / "reaction_chain_manifest.json")
            verify_reaction_chain_manifest(manifest, root=directory)
            return {
                "reaction_id": reaction_id,
                "reaction_plan": existing_plan,
                "chain_root": manifest["root_chain_digest"],
                "idempotent_replay": True,
            }
        created_at = _now()
        finding = build_finding(
            finding_id=f"finding-{key[:24]}",
            created_at=created_at,
            profile_ref=profile_ref,
            kind=evaluation.rule.finding_kind,
            severity=evaluation.rule.finding_severity,
            summary=evaluation.rule.finding_summary,
            observation=observation,
        )

        outcome = evaluation.outcome
        intent_ref = evaluation.intent_ref
        child_operation_id: str | None = None
        admission_status = "not_applicable"
        admission_decision: str | None = None
        admission_decision_id: str | None = None
        automation_binding: dict[str, Any] = unavailable_automation_binding(
            "automation_transition_not_applicable"
        )
        reason = evaluation.reason

        if outcome in {"run_intent", "retry_intent"} and intent_ref is not None:
            environment = load_environment(environment_path)
            policy_pack = compile_environment_policy_pack(environment.policy_pack)
            if policy_pack is None:
                outcome, intent_ref = "escalate", None
                admission_status, reason = "blocked", "reaction_policy_pack_required"
            else:
                intent_meta = profile.intent_metadata(intent_ref)
                risk = str(intent_meta.get("risk") or "low")
                verdict = evaluate_operation_policy(
                    policy_pack=policy_pack,
                    operation_id=reaction_id,
                    profile=profile.name,
                    environment=environment,
                    intent=intent_ref,
                    target=target,
                    mode=mode,
                    risk=risk,
                )
                admission_decision = verdict.decision
                admission_decision_id = verdict.verdict_id
                if not _plain_allow(verdict):
                    outcome, intent_ref = "escalate", None
                    admission_status = "blocked"
                    reason = f"govengine_reaction_blocked:{verdict.reason_code or verdict.decision}"
                else:
                    admission_status = "admitted"
                    plan_kwargs: dict[str, Any] = {
                        "profile_path": profile.root,
                        "environment_path": environment_path,
                        "intent": intent_ref,
                        "target": target,
                        "mode": mode,
                        "requested_by": f"reaction:{reaction_id}",
                    }
                    if catalog_runtime is not None:
                        catalog_path, catalog_target_id = catalog_runtime
                        plan_kwargs["catalog_path"] = catalog_path
                        plan_kwargs["target"] = catalog_target_id
                    child = self.controller.plan(
                        **plan_kwargs,
                    )
                    child_keys = child.metadata.get("idempotency")
                    if isinstance(child_keys, dict):
                        child_keys["reaction_child_plan_key"] = reaction_child_plan_key(
                            reaction_id=reaction_id,
                            child_operation_id=child.id,
                        )
                        child.metadata["idempotency"] = child_keys
                        self.controller.store.save_operation(child)
                    child_verdict = child.metadata.get("policy_verdict")
                    if (
                        not isinstance(child_verdict, dict)
                        or child_verdict.get("decision") != "allow"
                    ):
                        raise RExecOpValidationError(
                            "child operation policy drifted after reaction admission"
                        )
                    child_operation_id = child.id
                    draft_chain = _build_automation_chain(
                        reaction_id=reaction_id,
                        created_at=created_at,
                        profile_ref=profile_ref,
                        observation=observation,
                        finding=finding,
                        source_operation_id=_source_operation_id(observation),
                        source_operation_ref=_parent_operation_ref(
                            self.controller,
                            observation,
                        ),
                        source_intent=_source_intent_id(observation),
                        reaction_plan_ref={
                            "artifact_type": "reaction_plan",
                            "schema_version": "v0.1",
                            "schema_ref": "schemas/reaction_plan.v0.1.schema.json",
                            "digest": "",
                        },
                        child_operation_id=child.id,
                        child_intent=intent_ref,
                        depth=reaction_context.depth,
                        max_depth=pack.max_depth,
                        max_reactions=pack.max_reactions,
                        idempotency_key=key,
                        admission=None,
                        requires_govengine_admission=False,
                    )
                    if automation_transition_contract_available():
                        automation_binding = admit_automation_transition_request(
                            _automation_transition_request(
                                reaction_id=reaction_id,
                                chain_ref=_normalize_digest(artifact_sha256(draft_chain)),
                                parent_operation_id=_source_operation_id(observation),
                                parent_operation_ref=_parent_operation_ref(
                                    self.controller,
                                    observation,
                                ),
                                parent_intent=_source_intent_id(observation),
                                child_operation_id=child.id,
                                child_intent=intent_ref,
                                transition_reason=reason,
                                depth=reaction_context.depth + 1,
                                max_depth=pack.max_depth,
                                child_sequence=reaction_context.reaction_count + 1,
                                max_children=pack.max_reactions,
                            )
                        ).as_dict()
                        if automation_binding["status"] != "admitted":
                            raise RExecOpValidationError(
                                "automation transition admission denied: "
                                f"{automation_binding['reason_code']}"
                            )
                    else:
                        automation_binding = unavailable_automation_binding(
                            "govengine_automation_transition_contract_unavailable"
                        )

        plan = build_reaction_plan(
            reaction_id=reaction_id,
            created_at=created_at,
            profile_ref=profile_ref,
            rule_id=evaluation.rule.rule_id,
            rule_digest=evaluation.rule.digest,
            outcome=outcome,
            intent_ref=intent_ref,
            child_operation_id=child_operation_id,
            reason=reason,
            depth=reaction_context.depth,
            reaction_count=reaction_context.reaction_count,
            visited_rule_digests=reaction_context.visited_rule_digests,
            idempotency_key=key,
            admission_status=admission_status,
            admission_decision=admission_decision,
            admission_decision_id=admission_decision_id,
            observation=observation,
            finding=finding,
        )
        automation_chain: dict[str, Any] | None = None
        if child_operation_id:
            automation_admission = None
            if automation_binding.get("status") == "admitted":
                admission = automation_binding.get("admission")
                if isinstance(admission, Mapping):
                    automation_admission = {
                        "status": "admitted",
                        "decision_id": str(admission.get("decision_id") or ""),
                        "decision_digest": str(
                            automation_binding.get("admission_digest") or ""
                        ),
                        "owner_layer": "govengine",
                    }
            automation_chain = _build_automation_chain(
                reaction_id=reaction_id,
                created_at=created_at,
                profile_ref=profile_ref,
                observation=observation,
                finding=finding,
                source_operation_id=_source_operation_id(observation),
                source_operation_ref=_parent_operation_ref(self.controller, observation),
                source_intent=_source_intent_id(observation),
                reaction_plan_ref={
                    "artifact_type": "reaction_plan",
                    "schema_version": "v0.1",
                    "schema_ref": "schemas/reaction_plan.v0.1.schema.json",
                    "digest": artifact_sha256(plan),
                },
                child_operation_id=child_operation_id,
                child_intent=str(intent_ref or ""),
                depth=reaction_context.depth,
                max_depth=pack.max_depth,
                max_reactions=pack.max_reactions,
                idempotency_key=key,
                admission=automation_admission,
                requires_govengine_admission=automation_admission is not None,
            )
            verify_automation_chain(automation_chain)
            verify_runtime_automation_graph(automation_chain)
        secure_directory(directory)
        _write_json(directory / "01_observation.json", observation)
        _write_json(directory / "02_finding.json", finding)
        _write_json(directory / "03_reaction_plan.json", plan)
        if automation_chain is not None:
            _write_json(directory / "05_automation_chain.json", automation_chain)
            automation_binding["automation_chain_digest"] = _normalize_digest(
                artifact_sha256(automation_chain)
            )
            automation_binding["automation_chain_schema_ref"] = AUTOMATION_CHAIN_SCHEMA_REF
        manifest = build_reaction_chain_manifest(
            reaction_id=reaction_id,
            created_at=created_at,
            observation=observation,
            finding=finding,
            reaction_plan=plan,
        )
        _write_json(directory / "reaction_chain_manifest.json", manifest)
        return {
            "reaction_id": reaction_id,
            "reaction_plan": plan,
            "chain_root": manifest["root_chain_digest"],
            "automation_admission": automation_binding,
            "automation_chain": automation_chain or {},
        }

    def start(self, reaction_id: str) -> dict[str, Any]:
        directory = self.root / reaction_id
        plan = _read_json(directory / "03_reaction_plan.json")
        if plan.get("outcome") not in {"run_intent", "retry_intent"}:
            raise RExecOpValidationError("reaction has no executable child intent")
        child_id = str(plan.get("child_operation_id") or "")
        if not child_id or plan.get("admission", {}).get("status") != "admitted":
            raise RExecOpValidationError("reaction child was not admitted")
        operation = self.controller.start(child_id)
        validation = self.controller.validate(child_id)
        operation = self.controller.get_operation(child_id)
        receipt_path = (
            self.controller.store.operation_sclite_dir(child_id) / "05_execution_receipt.json"
        )
        if not receipt_path.is_file():
            raise RExecOpValidationError("child execution receipt was not emitted")
        receipt = _read_json(receipt_path)
        _write_json(directory / "04_execution_receipt.json", receipt)
        observation = _read_json(directory / "01_observation.json")
        finding = _read_json(directory / "02_finding.json")
        manifest = build_reaction_chain_manifest(
            reaction_id=reaction_id,
            created_at=str(plan["created_at"]),
            observation=observation,
            finding=finding,
            reaction_plan=plan,
            execution_receipt=receipt,
        )
        _write_json(directory / "reaction_chain_manifest.json", manifest)
        return {
            "reaction_id": reaction_id,
            "child_operation_id": child_id,
            "child_state": operation.state,
            "validation": validation,
            "chain_root": manifest["root_chain_digest"],
        }

    def replay(self, reaction_id: str) -> dict[str, Any]:
        directory = self.root / reaction_id
        manifest = _read_json(directory / "reaction_chain_manifest.json")
        return verify_reaction_chain_manifest(manifest, root=directory)

    def explain(self, reaction_id: str) -> dict[str, Any]:
        directory = self.root / reaction_id
        plan = _read_json(directory / "03_reaction_plan.json")
        manifest = _read_json(directory / "reaction_chain_manifest.json")
        replay = verify_reaction_chain_manifest(manifest, root=directory)
        observation = _read_json(directory / "01_observation.json")
        finding = _read_json(directory / "02_finding.json")
        receipt_path = directory / "04_execution_receipt.json"
        automation_chain_path = directory / "05_automation_chain.json"
        receipt_status = "present" if receipt_path.is_file() else "not_started_or_not_required"
        admission_raw = plan.get("admission")
        admission: Mapping[str, Any] = admission_raw if isinstance(admission_raw, Mapping) else {}
        profile_ref_raw = plan.get("profile_ref")
        profile_ref: Mapping[str, Any] = (
            profile_ref_raw if isinstance(profile_ref_raw, Mapping) else {}
        )
        replay_status = str(replay.get("status") or "")
        automation_chain: dict[str, Any] = {}
        automation_verification: dict[str, Any] = {"status": "absent"}
        runtime_graph_verification: dict[str, Any] = {"status": "absent"}
        if automation_chain_path.is_file():
            automation_chain = _read_json(automation_chain_path)
            automation_verification = verify_automation_chain(automation_chain)
            runtime_graph_verification = verify_runtime_automation_graph(automation_chain)
        automation_admission = _automation_admission_from_chain(automation_chain)
        return {
            "schema": "rexecop.reaction_explain.v0.1",
            "status": "verified" if replay_status in {"passed", "verified"} else "unverified",
            "reaction_id": reaction_id,
            "profile_ref": {
                "id": str(profile_ref.get("id") or ""),
                "version": str(profile_ref.get("version") or ""),
                "digest": str(profile_ref.get("digest") or ""),
            },
            "outcome": str(plan.get("outcome") or ""),
            "intent_ref": str(plan.get("intent_ref") or ""),
            "child_operation_id": str(plan.get("child_operation_id") or ""),
            "reason": str(plan.get("reason") or ""),
            "finding": {
                "id": str(finding.get("id") or finding.get("finding_id") or ""),
                "kind": str(finding.get("kind") or ""),
                "severity": str(finding.get("severity") or ""),
                "digest": artifact_sha256(finding),
            },
            "observation": {
                "digest": artifact_sha256(observation),
                "schema_ref": str(
                    observation.get("schema_ref") or observation.get("schema") or ""
                ),
                "source_operation_id": _source_operation_id(observation),
            },
            "admission": {
                "status": str(admission.get("status") or ""),
                "decision": str(admission.get("decision") or ""),
                "decision_id": str(admission.get("decision_id") or ""),
            },
            "automation_admission": automation_admission,
            "automation_chain": {
                "status": str(runtime_graph_verification.get("status") or "absent"),
                "schema_ref": str(
                    automation_verification.get("schema_ref")
                    or automation_chain.get("schema_ref")
                    or ""
                ),
                "root_digest": _normalize_digest(
                    str(
                        automation_verification.get("root_chain_digest")
                        or artifact_sha256(automation_chain)
                        if automation_chain
                        else ""
                    )
                ),
                "child_edge_count": int(automation_verification.get("child_edge_count") or 0),
                "sclite_bridge": automation_verification,
                "rexecop_graph": runtime_graph_verification,
            },
            "chain": {
                "root_digest": _normalize_digest(str(manifest.get("root_chain_digest") or "")),
                "manifest_digest": artifact_sha256(manifest),
                "receipt_status": receipt_status,
                "replay": replay,
            },
            "files": {
                "observation": "01_observation.json",
                "finding": "02_finding.json",
                "reaction_plan": "03_reaction_plan.json",
                "execution_receipt": (
                    "04_execution_receipt.json" if receipt_path.is_file() else ""
                ),
                "automation_chain": (
                    "05_automation_chain.json" if automation_chain_path.is_file() else ""
                ),
                "manifest": "reaction_chain_manifest.json",
            },
            "safe_next_actions": _reaction_safe_next_actions(reaction_id, plan, receipt_status),
            "non_claims": [
                "Explains persisted reaction artifacts without executing anything.",
                "Does not print raw observation facts, connector output, or secret values.",
                "SCLite reaction-chain artifacts remain the verification authority.",
            ],
        }

    def validate_proposal(self, *, profile_path: str | Path, proposal_path: Path) -> dict[str, Any]:
        profile = load_profile(resolve_profile_path(profile_path))
        proposal = _read_json(proposal_path)
        validate_escalation_proposal(proposal)
        outcome = str(proposal.get("suggested_outcome") or "")
        intent_ref = str(proposal.get("intent_ref") or "").strip() or None
        if outcome in {"run_intent", "retry_intent"}:
            if intent_ref is None:
                raise RExecOpValidationError("proposal intent_ref is required")
            _validate_reaction_intent(profile, intent_ref)
        elif intent_ref is not None:
            raise RExecOpValidationError("proposal intent_ref is only valid for intent outcomes")
        return {
            "status": "valid_untrusted_proposal",
            "proposal_digest": artifact_sha256(proposal),
            "may_execute": False,
            "requires_govengine_admission": True,
        }

    def _observation_from_operation(self, operation_id: str) -> dict[str, Any]:
        operation = self.controller.store.load_operation(operation_id)
        if operation.state != OperationState.COMPLETED.value:
            raise RExecOpValidationError("reaction observation requires completed operation")
        shared_state = operation.metadata.get("shared_state")
        if not isinstance(shared_state, Mapping):
            raise RExecOpValidationError("operation has no reaction observation")
        observation = shared_state.get("reaction_observation")
        if not isinstance(observation, dict):
            raise RExecOpValidationError("operation has no reaction observation")
        source = observation.get("source")
        if not isinstance(source, Mapping) or str(source.get("operation_id")) != operation.id:
            raise RExecOpValidationError("reaction observation source operation mismatch")
        return dict(observation)

    def _catalog_runtime_from_operation(self, operation_id: str) -> tuple[Path, str] | None:
        operation = self.controller.store.load_operation(operation_id)
        runtime = operation.metadata.get("catalog_runtime")
        if runtime is None:
            return None
        if not isinstance(runtime, Mapping):
            raise RExecOpValidationError("source operation catalog runtime is invalid")
        catalog_path = str(runtime.get("catalog_path") or "").strip()
        target_id = str(runtime.get("target_id") or "").strip()
        if not catalog_path or not target_id:
            raise RExecOpValidationError("source operation catalog runtime is incomplete")
        return Path(catalog_path), target_id


def _source_operation_id(observation: Mapping[str, Any]) -> str:
    source = observation.get("source")
    if not isinstance(source, Mapping):
        return ""
    return str(source.get("operation_id") or "")


def _source_intent_id(observation: Mapping[str, Any]) -> str:
    source = observation.get("source")
    if not isinstance(source, Mapping):
        return ""
    return str(source.get("intent_id") or "")


def _parent_operation_ref(
    controller: OperationController,
    observation: Mapping[str, Any],
) -> str:
    operation_id = _source_operation_id(observation)
    if operation_id:
        try:
            operation = controller.store.load_operation(operation_id)
        except Exception:
            operation = None
        if operation is not None:
            return _normalize_digest(artifact_sha256(operation.as_dict()))
    return _normalize_digest(artifact_sha256(dict(observation)))


def _automation_transition_request(
    *,
    reaction_id: str,
    chain_ref: str,
    parent_operation_id: str,
    parent_operation_ref: str,
    parent_intent: str,
    child_operation_id: str,
    child_intent: str,
    transition_reason: str,
    depth: int,
    max_depth: int,
    child_sequence: int,
    max_children: int,
) -> dict[str, Any]:
    return {
        "request_id": f"automation-transition:{reaction_id}",
        "chain_id": f"automation:{reaction_id}",
        "parent_operation_id": parent_operation_id or "external-observation",
        "parent_operation_ref": parent_operation_ref,
        "parent_intent": parent_intent or "unknown",
        "parent_status": "completed",
        "child_operation_id": child_operation_id,
        "child_intent": child_intent,
        "child_intent_class": "read_only",
        "transition_reason": transition_reason,
        "automation_chain_ref": chain_ref,
        "automation_chain_schema_ref": AUTOMATION_CHAIN_SCHEMA_REF,
        "source": "reaction",
        "depth": depth,
        "max_depth": max_depth,
        "child_sequence": child_sequence,
        "max_children": max_children,
        "allowed_child_intent_classes": ["read_only"],
        "llm_proposed": False,
        "llm_authority": False,
    }


def _build_automation_chain(
    *,
    reaction_id: str,
    created_at: str,
    profile_ref: Mapping[str, Any],
    observation: Mapping[str, Any],
    finding: Mapping[str, Any],
    source_operation_id: str,
    source_operation_ref: str,
    source_intent: str,
    reaction_plan_ref: Mapping[str, Any],
    child_operation_id: str,
    child_intent: str,
    depth: int,
    max_depth: int,
    max_reactions: int,
    idempotency_key: str,
    admission: Mapping[str, Any] | None,
    requires_govengine_admission: bool,
) -> dict[str, Any]:
    source_node = "source-operation"
    observation_node = "observation"
    finding_node = "finding"
    reaction_node = "reaction-plan"
    child_node = "child-operation"
    child_depth = depth + 1
    nodes = [
        automation_node(
            node_id=source_node,
            node_type="operation",
            depth=depth,
            status="completed",
            owner_layer="rexecop",
            authority_level="projection",
            operation_id=source_operation_id or "external-observation",
            artifact_ref={
                "artifact_type": "operation",
                "schema_version": "v0.1",
                "schema_ref": "rexecop.operation.v0.1",
                "digest": source_operation_ref,
            },
            labels=[source_intent or "source"],
        ),
        automation_node(
            node_id=observation_node,
            node_type="observation",
            depth=depth,
            status="completed",
            owner_layer="sclite",
            authority_level="canonical",
            artifact_ref={
                "artifact_type": str(observation.get("artifact_type") or "observation_envelope"),
                "schema_version": str(observation.get("schema_version") or "v0.1"),
                "schema_ref": str(
                    observation.get("schema_ref")
                    or "schemas/observation_envelope.v0.1.schema.json"
                ),
                "digest": _normalize_digest(artifact_sha256(observation)),
            },
            labels=["observation"],
        ),
        automation_node(
            node_id=finding_node,
            node_type="finding",
            depth=depth,
            status="completed",
            owner_layer="sclite",
            authority_level="canonical",
            artifact_ref={
                "artifact_type": str(finding.get("artifact_type") or "finding"),
                "schema_version": str(finding.get("schema_version") or "v0.1"),
                "schema_ref": str(finding.get("schema_ref") or "schemas/finding.v0.1.schema.json"),
                "digest": _normalize_digest(artifact_sha256(finding)),
            },
            labels=[str(finding.get("kind") or "finding")],
        ),
        automation_node(
            node_id=reaction_node,
            node_type="reaction_plan",
            depth=depth,
            status="planned",
            owner_layer="sclite",
            authority_level="canonical",
            artifact_ref=reaction_plan_ref,
            labels=["reaction"],
        ),
        automation_node(
            node_id=child_node,
            node_type="child_operation",
            depth=child_depth,
            status="admitted" if admission is not None else "planned",
            owner_layer="rexecop",
            authority_level="projection",
            operation_id=child_operation_id,
            labels=[child_intent, "read_only"],
        ),
    ]
    edges = [
        automation_edge(
            edge_id="source-observed",
            edge_type="observed",
            from_node=source_node,
            to_node=observation_node,
            depth=depth,
            labels=["source_observation"],
        ),
        automation_edge(
            edge_id="observation-detected",
            edge_type="detected",
            from_node=observation_node,
            to_node=finding_node,
            depth=depth,
            labels=["finding"],
        ),
        automation_edge(
            edge_id="finding-planned-reaction",
            edge_type="planned_reaction",
            from_node=finding_node,
            to_node=reaction_node,
            depth=depth,
            labels=["reaction_plan"],
        ),
        automation_edge(
            edge_id="reaction-admitted-child",
            edge_type="admitted_child",
            from_node=reaction_node,
            to_node=child_node,
            depth=child_depth,
            idempotency_key=idempotency_key,
            admission=admission,
            labels=["child_operation"],
        ),
    ]
    return build_automation_chain(
        chain_id=f"automation:{reaction_id}",
        created_at=created_at,
        profile_ref=profile_ref,
        source_operation_id=source_operation_id or "external-observation",
        controls={
            "max_depth": max_depth,
            "max_nodes": 16,
            "max_reactions": max_reactions,
            "requires_govengine_admission": requires_govengine_admission,
            "requires_profile_transition": True,
            "allowed_child_intent_classes": ["read_only"],
            "llm_may_execute": False,
        },
        recovery={
            "append_mode": "append_only",
            "idempotency_scope": "chain_edge",
            "duplicate_child_policy": "reuse_existing_child",
            "replay_policy": "verify_before_execute",
            "checkpoint_required": True,
        },
        compatibility={
            "reaction_chain_v0_1_subset": True,
            "single_step_reaction_compatible": True,
        },
        nodes=nodes,
        edges=edges,
    )


def _automation_admission_from_chain(automation_chain: Mapping[str, Any]) -> dict[str, str]:
    for edge in automation_chain.get("edges") or []:
        if not isinstance(edge, Mapping):
            continue
        if edge.get("edge_type") != "admitted_child":
            continue
        admission = edge.get("admission")
        if isinstance(admission, Mapping):
            return {
                "status": str(admission.get("status") or ""),
                "decision_id": str(admission.get("decision_id") or ""),
                "decision_digest": str(admission.get("decision_digest") or ""),
                "owner_layer": str(admission.get("owner_layer") or ""),
            }
    return {
        "status": "absent",
        "decision_id": "",
        "decision_digest": "",
        "owner_layer": "",
    }


def _normalize_digest(value: str) -> str:
    digest = str(value or "").strip()
    if not digest:
        return ""
    return digest if digest.startswith("sha256:") else f"sha256:{digest}"


def _reaction_safe_next_actions(
    reaction_id: str,
    plan: Mapping[str, Any],
    receipt_status: str,
) -> list[str]:
    actions = [f"rexecop reaction explain --reaction {reaction_id}"]
    if plan.get("outcome") in {"run_intent", "retry_intent"}:
        child_id = str(plan.get("child_operation_id") or "")
        if child_id:
            actions.append(f"rexecop chain explain {child_id}")
        if receipt_status != "present":
            actions.append(f"rexecop reaction-start --reaction {reaction_id}")
    actions.append(f"rexecop reaction-replay --reaction {reaction_id}")
    return actions
