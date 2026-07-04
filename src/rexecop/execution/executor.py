from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.runtime import ConnectorDispatcher
from rexecop.errors import RExecOpValidationError
from rexecop.evidence.redaction import redact_payload, redact_text
from rexecop.execution.backend import StepExecutionContext, StepExecutionResult
from rexecop.execution.govengine_governance import enforce_typed_execution_governance
from rexecop.execution.internal_registry import InternalHandler, load_internal_handlers
from rexecop.execution.typed_spec import bind_step_execution_spec, compile_step_execution_spec
from rexecop.profile.loader import load_profile

EvidenceHandler = Callable[[StepExecutionContext], dict[str, Any]]

__all__ = ["StepExecutor"]


class StepExecutor:
    def __init__(
        self,
        connector_dispatcher: ConnectorDispatcher | None = None,
        *,
        evidence_handler: EvidenceHandler | None = None,
        internal_handlers: Mapping[str, InternalHandler] | None = None,
    ) -> None:
        self.connector_dispatcher = connector_dispatcher or ConnectorDispatcher()
        self.evidence_handler = evidence_handler
        self._internal_handlers = load_internal_handlers(extra=internal_handlers)

    def execute(self, context: StepExecutionContext) -> StepExecutionResult:
        step_id = str(context.step.get("id") or "")
        step_type = str(context.step.get("type") or "internal")
        action = str(context.step.get("action") or "")
        state_before = deepcopy(context.shared_state)

        try:
            if step_type == "connector":
                result = self._execute_connector(context, step_id, action)
            elif step_type == "evidence":
                result = self._execute_evidence(context, step_id, action)
            else:
                result = self._execute_internal(context, step_id, action)
            bounded = self._apply_output_controls(context, result, state_before=state_before)
            if bounded.success:
                self._store_bounded_result(context, step_type, bounded)
            return bounded
        except Exception as exc:  # noqa: BLE001 - step boundary
            context.shared_state.clear()
            context.shared_state.update(state_before)
            return StepExecutionResult(
                step_id=step_id,
                success=False,
                output={},
                error=redact_text(str(exc)),
            )

    def _execute_connector(
        self,
        context: StepExecutionContext,
        step_id: str,
        action: str,
    ) -> StepExecutionResult:
        connector = str(context.step.get("connector") or "")
        try:
            spec = self._bind_typed_execution_spec(context, step_id=step_id)
        except RExecOpValidationError as exc:
            return StepExecutionResult(
                step_id=step_id,
                success=False,
                output={
                    "error_class": connector_errors.VALIDATION_FAILED,
                },
                error=redact_text(str(exc)),
            )
        if spec is not None:
            admission = enforce_typed_execution_governance(
                spec=spec,
                operation_id=context.operation_id,
                mode=context.mode,
                shared_state=context.shared_state,
            )
            if not admission["allowed"]:
                return StepExecutionResult(
                    step_id=step_id,
                    success=False,
                    output={
                        "error_class": connector_errors.POLICY_DENIED,
                        "policy_reason_code": admission["reason_code"],
                        "policy_blockers": list(admission.get("blockers") or []),
                        "typed_execution_admission": dict(admission),
                    },
                    error=redact_text(
                        "typed execution governance denied: "
                        + str(admission.get("reason_code") or "denied")
                    ),
                )
        response = self.connector_dispatcher.invoke(
            ConnectorRequest(
                connector=connector,
                action=action,
                target=context.target,
                mode=context.mode,
                metadata={
                    "execution_controls": dict(
                        context.shared_state.get("execution_controls") or {}
                    )
                },
            )
        )
        if not response.success:
            output = redact_payload(response.as_dict())
            error_class = str(response.data.get("error_class") or "")
            if error_class:
                output["error_class"] = error_class
            return StepExecutionResult(
                step_id=step_id,
                success=False,
                output=output,
                error=redact_text(response.error),
            )
        output = redact_payload(response.as_dict())
        before_state = response.data.get("before_state")
        after_state = response.data.get("after_state")
        if isinstance(before_state, dict):
            output["before_state"] = before_state
        if isinstance(after_state, dict):
            output["after_state"] = after_state
        return StepExecutionResult(step_id=step_id, success=True, output=output)

    def _execute_internal(
        self,
        context: StepExecutionContext,
        step_id: str,
        action: str,
    ) -> StepExecutionResult:
        handler = self._internal_handlers.get(action)
        if handler is None:
            return StepExecutionResult(
                step_id=step_id,
                success=False,
                output={},
                error=f"internal_action_not_registered:{action}",
            )
        output = handler(context)
        return StepExecutionResult(step_id=step_id, success=True, output=output)

    def _execute_evidence(
        self,
        context: StepExecutionContext,
        step_id: str,
        action: str,
    ) -> StepExecutionResult:
        if action == "produce_receipt" and self.evidence_handler is not None:
            output = self.evidence_handler(context)
            return StepExecutionResult(step_id=step_id, success=True, output=output)
        return StepExecutionResult(
            step_id=step_id,
            success=True,
            output={"action": action, "status": "recorded"},
        )

    def _apply_output_controls(
        self,
        context: StepExecutionContext,
        result: StepExecutionResult,
        *,
        state_before: dict[str, Any],
    ) -> StepExecutionResult:
        controls = context.shared_state.get("execution_controls")
        raw_controls = controls if isinstance(controls, Mapping) else {}
        max_output_bytes = int(raw_controls.get("max_output_bytes") or 65536)
        state_delta = {
            key: value
            for key, value in context.shared_state.items()
            if key not in state_before or state_before[key] != value
        }
        canonical = json.dumps(
            {"output": result.output, "state_delta": state_delta},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
        digest = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
        if len(canonical) > max_output_bytes:
            context.shared_state.clear()
            context.shared_state.update(state_before)
            return StepExecutionResult(
                step_id=result.step_id,
                success=False,
                output={
                    "error_class": connector_errors.VALIDATION_FAILED,
                    "output_digests": {"record": digest},
                    "output_truncated": {"record": True},
                    "output_sizes": {"record_bytes": len(canonical)},
                    "max_output_bytes": max_output_bytes,
                },
                error="execution output exceeds policy limit",
            )
        output = dict(result.output)
        digests = output.get("output_digests")
        merged = dict(digests) if isinstance(digests, Mapping) else {}
        merged["record"] = digest
        output["output_digests"] = merged
        return StepExecutionResult(
            step_id=result.step_id,
            success=result.success,
            output=output,
            error=result.error,
        )

    def _bind_typed_execution_spec(
        self,
        context: StepExecutionContext,
        *,
        step_id: str,
    ) -> dict[str, Any] | None:
        execution_context = context.shared_state.get("execution_context")
        if not isinstance(execution_context, dict):
            return None
        profile_root = str(execution_context.get("profile_root") or "").strip()
        connectors = execution_context.get("connectors")
        connector = str(context.step.get("connector") or "").strip()
        if not profile_root or not isinstance(connectors, dict):
            return None
        connector_config = connectors.get(connector)
        if not isinstance(connector_config, dict):
            raise RExecOpValidationError(f"connector not configured: {connector}")
        spec = compile_step_execution_spec(
            step=context.step,
            profile=load_profile(Path(profile_root)),
            connector_config=connector_config,
            mode=context.mode,
        )
        bind_step_execution_spec(
            step_id=step_id,
            spec=spec,
            shared_state=context.shared_state,
        )
        return spec

    def _store_bounded_result(
        self,
        context: StepExecutionContext,
        step_type: str,
        result: StepExecutionResult,
    ) -> None:
        if step_type == "internal":
            context.shared_state.setdefault("internal_results", {})[result.step_id] = dict(
                result.output
            )
            return
        if step_type != "connector":
            return
        data = result.output.get("data")
        bounded_data = dict(data) if isinstance(data, Mapping) else {}
        context.shared_state.setdefault("connector_results", {})[result.step_id] = bounded_data
        before_state = bounded_data.get("before_state")
        after_state = bounded_data.get("after_state")
        if isinstance(before_state, dict) and isinstance(after_state, dict):
            context.shared_state.setdefault("mutation_states", {})[result.step_id] = {
                "before_state": before_state,
                "after_state": after_state,
            }
