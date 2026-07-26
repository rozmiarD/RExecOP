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
from rexecop.errors import RExecOpError, RExecOpValidationError
from rexecop.evidence.redaction import redact_payload, redact_text
from rexecop.execution.backend import StepExecutionContext, StepExecutionResult
from rexecop.execution.govengine_governance import enforce_typed_execution_governance
from rexecop.execution.internal_registry import InternalHandler, load_internal_handlers
from rexecop.execution.model import validated_max_output_bytes
from rexecop.execution.typed_spec import bind_step_execution_spec, compile_step_execution_spec
from rexecop.profile.loader import load_profile
from rexecop.runtime.mutation_posture import require_mutation_execution_enabled

EvidenceHandler = Callable[[StepExecutionContext], dict[str, Any]]
AttemptStartHandler = Callable[[StepExecutionContext, dict[str, Any] | None], dict[str, Any]]
AttemptPreIOHandler = Callable[[dict[str, Any]], None]
AttemptFinishHandler = Callable[[dict[str, Any], str, StepExecutionResult | None], None]
AttemptReceiptHandler = Callable[
    [dict[str, Any], StepExecutionResult],
    StepExecutionResult,
]

_EXCLUDED_OUTPUT_STATE_DELTA_KEYS = frozenset(
    {
        "execution_context",
        "execution_controls",
        "execution_request",
        "typed_execution_governance",
        "typed_execution_specs",
        "typed_execution_admissions",
    }
)
_OUTPUT_LIMIT_EVIDENCE_SCHEMA = "rexecop.output_limit_evidence.v0.1"
_OUTPUT_LIMIT_EVIDENCE_MAX_BYTES = 2048
_MISSING = object()

__all__ = ["StepExecutor"]


class StepExecutor:
    def __init__(
        self,
        connector_dispatcher: ConnectorDispatcher | None = None,
        *,
        evidence_handler: EvidenceHandler | None = None,
        attempt_start_handler: AttemptStartHandler | None = None,
        attempt_pre_io_handler: AttemptPreIOHandler | None = None,
        attempt_finish_handler: AttemptFinishHandler | None = None,
        attempt_receipt_handler: AttemptReceiptHandler | None = None,
        internal_handlers: Mapping[str, InternalHandler] | None = None,
    ) -> None:
        self.connector_dispatcher = connector_dispatcher or ConnectorDispatcher()
        self.evidence_handler = evidence_handler
        self.attempt_start_handler = attempt_start_handler
        self.attempt_pre_io_handler = attempt_pre_io_handler
        self.attempt_finish_handler = attempt_finish_handler
        self.attempt_receipt_handler = attempt_receipt_handler
        self._internal_handlers = load_internal_handlers(extra=internal_handlers)

    def execute(self, context: StepExecutionContext) -> StepExecutionResult:
        step_id = str(context.step.get("id") or "")
        step_type = str(context.step.get("type") or "internal")
        action = str(context.step.get("action") or "")
        state_before = deepcopy(context.shared_state)
        attempt: dict[str, Any] | None = None
        deferred_attempt_finish = False

        try:
            if step_type == "connector":
                result, attempt, deferred_attempt_finish = self._execute_connector(
                    context,
                    step_id,
                    action,
                )
            elif step_type == "evidence":
                result = self._execute_evidence(context, step_id, action)
            else:
                result = self._execute_internal(context, step_id, action)
            bounded = self._apply_output_controls(context, result, state_before=state_before)
            if (
                deferred_attempt_finish
                and attempt is not None
                and self.attempt_finish_handler is not None
            ):
                self.attempt_finish_handler(attempt, "failed", bounded)
            if attempt is not None and self.attempt_receipt_handler is not None:
                bounded = self.attempt_receipt_handler(attempt, bounded)
            if bounded.success:
                self._store_bounded_result(context, step_type, bounded)
            return bounded
        except Exception as exc:  # noqa: BLE001 - step boundary
            context.shared_state.clear()
            context.shared_state.update(state_before)
            if isinstance(exc, RExecOpError):
                reason_code = str(getattr(exc, "reason_code", "runtime_error"))
                message = str(getattr(exc, "public_message", "runtime operation failed"))
            else:
                reason_code = "internal_error"
                message = "connector execution failed"
            return StepExecutionResult(
                step_id=step_id,
                success=False,
                output={"error_class": reason_code, "reason_code": reason_code},
                error=message,
            )

    def _execute_connector(
        self,
        context: StepExecutionContext,
        step_id: str,
        action: str,
    ) -> tuple[StepExecutionResult, dict[str, Any] | None, bool]:
        connector = str(context.step.get("connector") or "")
        try:
            spec = self._bind_typed_execution_spec(context, step_id=step_id)
        except RExecOpValidationError as exc:
            return (
                StepExecutionResult(
                    step_id=step_id,
                    success=False,
                    output={
                        "error_class": connector_errors.VALIDATION_FAILED,
                    },
                    error=redact_text(str(exc)),
                ),
                None,
                False,
            )
        if spec is not None:
            admission = enforce_typed_execution_governance(
                spec=spec,
                operation_id=context.operation_id,
                mode=context.mode,
                shared_state=context.shared_state,
            )
            if not admission["allowed"]:
                return (
                    StepExecutionResult(
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
                    ),
                    None,
                    False,
                )
        require_mutation_execution_enabled(context.mode)
        attempt = (
            self.attempt_start_handler(context, spec)
            if self.attempt_start_handler is not None
            else None
        )
        if attempt is not None and self.attempt_pre_io_handler is not None:
            try:
                self.attempt_pre_io_handler(attempt)
            except Exception as exc:  # noqa: BLE001 - fail closed before connector I/O
                if isinstance(exc, RExecOpError):
                    reason_code = str(getattr(exc, "reason_code", "runtime_error"))
                    message = str(
                        getattr(exc, "public_message", "runtime operation failed")
                    )
                else:
                    reason_code = "internal_error"
                    message = "pre-I/O execution validation failed"
                result = StepExecutionResult(
                    step_id=step_id,
                    success=False,
                    output={
                        "error_class": reason_code,
                        "reason_code": reason_code,
                    },
                    error=message,
                )
                if self.attempt_finish_handler is not None:
                    self.attempt_finish_handler(attempt, "failed", result)
                return result, None, False
        try:
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
        except BaseException:
            if attempt is not None and self.attempt_finish_handler is not None:
                self.attempt_finish_handler(attempt, "indeterminate", None)
            raise
        if _is_connector_output_limit_candidate(response):
            result = StepExecutionResult(
                step_id=step_id,
                success=False,
                output={
                    "error_class": "output_limit_exceeded",
                    "data": response.data,
                },
                error="connector output limit exceeded",
            )
            return result, attempt, True
        if not response.success:
            output = redact_payload(response.as_dict())
            error_class = str(response.data.get("error_class") or "")
            if error_class:
                output["error_class"] = error_class
            result = StepExecutionResult(
                step_id=step_id,
                success=False,
                output=output,
                error=redact_text(response.error),
            )
            if attempt is not None and self.attempt_finish_handler is not None:
                self.attempt_finish_handler(attempt, "failed", result)
            return result, attempt, False
        output = redact_payload(response.as_dict())
        before_state = response.data.get("before_state")
        after_state = response.data.get("after_state")
        if isinstance(before_state, dict):
            output["before_state"] = before_state
        if isinstance(after_state, dict):
            output["after_state"] = after_state
        result = StepExecutionResult(step_id=step_id, success=True, output=output)
        if attempt is not None and self.attempt_finish_handler is not None:
            self.attempt_finish_handler(attempt, "completed", result)
        return result, attempt, False

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
        max_output_bytes = validated_max_output_bytes(
            raw_controls["max_output_bytes"]
            if "max_output_bytes" in raw_controls
            else 65536
        )
        if _is_output_limit_candidate(result):
            context.shared_state.clear()
            context.shared_state.update(state_before)
            try:
                overflow_evidence = _bounded_output_limit_evidence(
                    result.output,
                    active_max_output_bytes=max_output_bytes,
                )
            except Exception:  # noqa: BLE001 - untrusted evidence must fail closed
                overflow_evidence = None
            if overflow_evidence is None:
                return StepExecutionResult(
                    step_id=result.step_id,
                    success=False,
                    output=_invalid_output_limit_evidence(),
                    error="execution output failed overflow evidence validation",
                )
            try:
                canonical = json.dumps(
                    {
                        "output": redact_payload(result.output),
                        "state_delta": {},
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    default=str,
                ).encode("utf-8")
                digest = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
                overflow_evidence = _bounded_output_limit_evidence(
                    result.output,
                    active_max_output_bytes=max_output_bytes,
                    record_digest=digest,
                    record_bytes=len(canonical),
                )
            except Exception:  # noqa: BLE001 - untrusted evidence must fail closed
                overflow_evidence = None
            if overflow_evidence is None:
                return StepExecutionResult(
                    step_id=result.step_id,
                    success=False,
                    output=_invalid_output_limit_evidence(),
                    error="execution output failed overflow evidence validation",
                )
            return StepExecutionResult(
                step_id=result.step_id,
                success=False,
                output=overflow_evidence,
                error="connector output limit exceeded",
            )
        state_delta = {
            key: value
            for key, value in context.shared_state.items()
            if key not in _EXCLUDED_OUTPUT_STATE_DELTA_KEYS
            and (key not in state_before or state_before[key] != value)
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
                output=_generic_overflow_evidence(
                    record_digest=digest,
                    record_bytes=len(canonical),
                    producer_max_bytes=max_output_bytes,
                ),
                error="execution output exceeds policy limit",
            )
        output = dict(result.output)
        digests = output.get("output_digests")
        merged = dict(digests) if isinstance(digests, Mapping) else {}
        merged["record"] = digest
        output["output_digests"] = merged
        output["output_sizes"] = {"record_bytes": len(canonical)}
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


def _bounded_output_limit_evidence(
    output: Mapping[str, Any],
    *,
    active_max_output_bytes: int,
    record_digest: str | None = None,
    record_bytes: int | None = None,
) -> dict[str, Any] | None:
    if type(output) is not dict:
        return None
    output_error_class = _exact_dict_value(output, "error_class")
    if (
        type(output_error_class) is not str
        or output_error_class != "output_limit_exceeded"
    ):
        return None
    raw_data = _exact_dict_value(output, "data")
    if type(raw_data) is not dict:
        return None
    data_error_class = _exact_dict_value(raw_data, "error_class")
    if (
        type(data_error_class) is not str
        or data_error_class != "output_limit_exceeded"
        or _exact_dict_value(raw_data, "output_limit_exceeded") is not True
    ):
        return None
    raw_digests = _exact_dict_value(raw_data, "output_digests")
    raw_sizes = _exact_dict_value(raw_data, "output_sizes")
    raw_truncated = _exact_dict_value(raw_data, "output_truncated")
    if not (
        type(raw_digests) is dict
        and type(raw_sizes) is dict
        and type(raw_truncated) is dict
    ):
        return None
    stdout_digest = _exact_dict_value(raw_digests, "stdout")
    stderr_digest = _exact_dict_value(raw_digests, "stderr")
    if (
        type(stdout_digest) is not str
        or type(stderr_digest) is not str
        or not _is_sha256_digest(stdout_digest)
        or not _is_sha256_digest(stderr_digest)
    ):
        return None
    stdout_bytes = _exact_nonnegative_int(
        _exact_dict_value(raw_sizes, "stdout_bytes")
    )
    stderr_bytes = _exact_nonnegative_int(
        _exact_dict_value(raw_sizes, "stderr_bytes")
    )
    total_bytes = _exact_nonnegative_int(_exact_dict_value(raw_sizes, "total_bytes"))
    output_limit = _exact_positive_int(
        _exact_dict_value(raw_data, "max_output_bytes")
    )
    stdout_truncated = _exact_dict_value(raw_truncated, "stdout")
    stderr_truncated = _exact_dict_value(raw_truncated, "stderr")
    if (
        stdout_bytes is None
        or stderr_bytes is None
        or total_bytes is None
        or output_limit is None
        or type(stdout_truncated) is not bool
        or type(stderr_truncated) is not bool
    ):
        return None
    if output_limit > active_max_output_bytes:
        return None
    if total_bytes != stdout_bytes + stderr_bytes or total_bytes <= output_limit:
        return None
    if not stdout_truncated and not stderr_truncated:
        return None
    if stdout_bytes > output_limit and stdout_truncated is not True:
        return None
    if stderr_bytes > output_limit and stderr_truncated is not True:
        return None
    if (stdout_truncated and stdout_bytes == 0) or (
        stderr_truncated and stderr_bytes == 0
    ):
        return None
    evidence: dict[str, Any] = {
        "error_class": "output_limit_exceeded",
        "output_limit_exceeded": True,
        "output_digests": {
            "stdout": stdout_digest,
            "stderr": stderr_digest,
        },
        "output_truncated": {
            "stdout": stdout_truncated,
            "stderr": stderr_truncated,
        },
        "output_sizes": {
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "total_bytes": total_bytes,
        },
        "max_output_bytes": output_limit,
    }
    if record_digest is not None and record_bytes is not None:
        evidence["output_digests"]["record"] = record_digest
        evidence["output_truncated"]["record"] = True
        evidence["output_sizes"]["record_bytes"] = record_bytes
    return _with_output_limit_evidence_envelope(evidence)


def _is_connector_output_limit_candidate(response: Any) -> bool:
    if response.success is not False or type(response.data) is not dict:
        return False
    output_limit_exceeded = _exact_dict_value(response.data, "output_limit_exceeded")
    error_class = _exact_dict_value(response.data, "error_class")
    return output_limit_exceeded is True or (
        type(error_class) is str
        and error_class == "output_limit_exceeded"
    )


def _is_output_limit_candidate(result: StepExecutionResult) -> bool:
    if result.success is not False or type(result.output) is not dict:
        return False
    error_class = _exact_dict_value(result.output, "error_class")
    raw_data = _exact_dict_value(result.output, "data")
    return (
        type(error_class) is str
        and error_class == "output_limit_exceeded"
        and type(raw_data) is dict
    )


def _exact_dict_value(value: Any, key: str) -> Any:
    if type(value) is not dict:
        return _MISSING
    for candidate_key, item in value.items():
        if type(candidate_key) is str and candidate_key == key:
            return item
    return _MISSING


def _invalid_output_limit_evidence() -> dict[str, Any]:
    bounded = _with_output_limit_evidence_envelope(
        {"error_class": connector_errors.VALIDATION_FAILED}
    )
    if bounded is None:  # pragma: no cover - fixed fields are well below the cap
        raise RExecOpValidationError("overflow evidence envelope exceeds fixed limit")
    return bounded


def _generic_overflow_evidence(
    *,
    record_digest: str,
    record_bytes: int,
    producer_max_bytes: int,
) -> dict[str, Any]:
    evidence = {
        "error_class": connector_errors.VALIDATION_FAILED,
        "output_digests": {"record": record_digest},
        "output_truncated": {"record": True},
        "output_sizes": {"record_bytes": record_bytes},
        "max_output_bytes": producer_max_bytes,
    }
    bounded = _with_output_limit_evidence_envelope(evidence)
    if bounded is None:  # pragma: no cover - fixed fields are well below the cap
        raise RExecOpValidationError("overflow evidence envelope exceeds fixed limit")
    return bounded


def _with_output_limit_evidence_envelope(
    evidence: dict[str, Any],
) -> dict[str, Any] | None:
    bounded = dict(evidence)
    envelope = {
        "schema": _OUTPUT_LIMIT_EVIDENCE_SCHEMA,
        "max_bytes": _OUTPUT_LIMIT_EVIDENCE_MAX_BYTES,
        "evidence_bytes": 0,
    }
    bounded["overflow_evidence_envelope"] = envelope
    for _ in range(8):
        actual = len(_canonical_output_bytes(bounded))
        if envelope["evidence_bytes"] == actual:
            return bounded if actual <= _OUTPUT_LIMIT_EVIDENCE_MAX_BYTES else None
        envelope["evidence_bytes"] = actual
    return None


def _canonical_output_bytes(output: Mapping[str, Any]) -> bytes:
    return json.dumps(
        output,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _is_sha256_digest(value: str) -> bool:
    prefix = "sha256:"
    payload = value.removeprefix(prefix)
    return value.startswith(prefix) and len(payload) == 64 and all(
        char in "0123456789abcdef" for char in payload
    )


def _exact_nonnegative_int(value: Any) -> int | None:
    if type(value) is not int:
        return None
    return value if value >= 0 else None


def _exact_positive_int(value: Any) -> int | None:
    normalized = _exact_nonnegative_int(value)
    return normalized if normalized is not None and normalized > 0 else None
