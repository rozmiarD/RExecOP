from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sclite import (
    build_finding,
    build_reaction_chain_manifest,
    build_reaction_plan,
    reaction_idempotency_key,
    validate_escalation_proposal,
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
from rexecop.reaction.compiler import compile_reaction_pack
from rexecop.reaction.evaluator import evaluate_reaction
from rexecop.reaction.model import ReactionContext
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
        if source_operation_id is not None:
            observation = self._observation_from_operation(source_operation_id)
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
                    child = self.controller.plan(
                        profile_path=profile.root,
                        environment_path=environment_path,
                        intent=intent_ref,
                        target=target,
                        mode=mode,
                        requested_by=f"reaction:{reaction_id}",
                    )
                    child_verdict = child.metadata.get("policy_verdict")
                    if (
                        not isinstance(child_verdict, dict)
                        or child_verdict.get("decision") != "allow"
                    ):
                        raise RExecOpValidationError(
                            "child operation policy drifted after reaction admission"
                        )
                    child_operation_id = child.id

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
        secure_directory(directory)
        _write_json(directory / "01_observation.json", observation)
        _write_json(directory / "02_finding.json", finding)
        _write_json(directory / "03_reaction_plan.json", plan)
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
