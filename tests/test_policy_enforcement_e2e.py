from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from rexecop.catalog.digest import canonical_digest
from rexecop.connectors.base import ConnectorRequest, ConnectorResponse
from rexecop.connectors.local_shell import LocalShellReadonlyRuntime
from rexecop.connectors.runtime import ConnectorDispatcher
from rexecop.errors import RExecOpValidationError
from rexecop.execution.backend import StepExecutionContext, StepExecutionResult
from rexecop.execution.executor import StepExecutor
from rexecop.execution.model import (
    execution_receipt_digest,
    execution_receipt_from_results,
    execution_request_digest,
    execution_request_from_workflow,
)
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore
from rexecop.workflow.runner import WorkflowRunner, WorkflowRunResult

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def _policy_environment(
    tmp_path: Path,
    *,
    max_steps: int = 20,
    output_limit: int | None = 8192,
) -> Path:
    constraints = [
        {
            "constraint_id": "steps",
            "kind": "max_steps",
            "value": max_steps,
        }
    ]
    if output_limit is not None:
        constraints.append(
            {
                "constraint_id": "output",
                "kind": "output_limit",
                "value": output_limit,
            }
        )
    data = yaml.safe_load(ENVIRONMENT.read_text())
    data["environment"]["policy_pack"] = {
        "policy_id": "b2-runtime-controls",
        "version": "1",
        "rules": [
            {
                "rule_id": "bounded-operation",
                "priority": 10,
                "effect": "allow_with_obligations",
                "conditions": {
                    "action.category": "operation",
                    "action.mode": "read",
                    "action.intent": "inspect_fixture_state",
                },
                "obligations": [
                    {"obligation_id": "receipt", "kind": "receipt"},
                    {
                        "obligation_id": "output-digests",
                        "kind": "output_digest_required",
                    },
                ],
                "constraints": constraints,
            },
            {
                "rule_id": "allow-read-connectors",
                "priority": 20,
                "effect": "allow",
                "conditions": {
                    "action.category": "connector",
                    "action.mode": "read",
                },
            },
        ],
    }
    path = tmp_path / "environment.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_policy_binding_flows_to_request_receipt_and_sclite(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=_policy_environment(tmp_path),
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )

    completed = controller.start(operation.id)

    assert completed.state == OperationState.COMPLETED.value
    state = completed.metadata["shared_state"]
    request = state["execution_request"]
    receipt = state["execution_receipt"]
    enforcement = completed.metadata["policy_enforcement"]
    assert (
        request["policy_binding"]["admission_digest"]
        == enforcement["admission_digest"]
    )
    assert (
        request["policy_binding"]["enforcement_plan_digest"]
        == enforcement["plan_digest"]
    )
    assert receipt["policy_binding"] == request["policy_binding"]
    assert receipt["typed_execution_binding"]["step_digests"]
    assert receipt["enforcement"]["typed_execution_specs_bound"] is True
    assert receipt["request_digest"].startswith("sha256:")
    assert receipt["receipt_digest"].startswith("sha256:")
    assert receipt["enforcement"]["status"] == "enforced"
    assert receipt["enforcement"]["output_digests_verified"] is True

    exported = controller.export_receipt(operation.id)
    bundle = Path(str(exported["bundle_dir"]))
    execution_contract = json.loads(
        (bundle / "03_execution_contract.json").read_text()
    )
    sclite_receipt = json.loads((bundle / "05_execution_receipt.json").read_text())
    assert (
        execution_contract["policy_enforcement"]["admission_digest"]
        == enforcement["admission_digest"]
    )
    assert sclite_receipt["policy_enforcement"]["status"] == "enforced"
    assert sclite_receipt["rexecop_runtime_binding"]["typed_execution_binding"]["step_digests"]


def test_omitted_governance_output_limit_uses_local_safe_default(
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=_policy_environment(tmp_path, output_limit=None),
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )

    governance_controls = operation.metadata["policy_enforcement"]["plan"]["controls"]
    assert governance_controls["max_output_bytes"] == 0

    completed = controller.start(operation.id)

    assert completed.state == OperationState.COMPLETED.value
    state = completed.metadata["shared_state"]
    assert state["execution_request"]["resource_limits"]["max_output_bytes"] == 65536
    assert state["execution_controls"]["max_output_bytes"] == 65536
    assert (
        state["execution_receipt"]["enforcement"]["resource_limits"][
            "max_output_bytes"
        ]
        == 65536
    )


def test_policy_verdict_drift_blocks_before_executor(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=_policy_environment(tmp_path),
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    operation.metadata["policy_verdict"]["reason_code"] = "tampered"
    store.save_operation(operation)

    with patch("rexecop.execution.executor.StepExecutor.execute") as execute:
        with pytest.raises(
            RExecOpValidationError,
            match="policy_enforcement_plan_drift",
        ):
            controller.start(operation.id)

    execute.assert_not_called()


def test_policy_admission_drift_blocks_before_state_transition(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=_policy_environment(tmp_path),
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    operation.metadata["policy_enforcement"]["admission"]["reason_code"] = "tampered"
    store.save_operation(operation)

    with patch("rexecop.execution.executor.StepExecutor.execute") as execute:
        with pytest.raises(
            RExecOpValidationError,
            match="policy_enforcement_admission_drift",
        ):
            controller.start(operation.id)

    execute.assert_not_called()
    assert store.load_operation(operation.id).state == OperationState.PLANNED.value


def test_policy_max_steps_blocks_before_executor(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=_policy_environment(tmp_path, max_steps=1),
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )

    with patch("rexecop.execution.executor.StepExecutor.execute") as execute:
        with pytest.raises(
            RExecOpValidationError,
            match="max_steps is lower than planned workflow",
        ):
            controller.start(operation.id)

    execute.assert_not_called()


def test_output_limit_replaces_oversized_payload_with_bounded_digest() -> None:
    secret_marker = "sensitive-marker-" * 200
    runner = WorkflowRunner(
        StepExecutor(internal_handlers={"large": lambda _context: {"value": secret_marker}})
    )

    result = runner.run(
        operation_id="op-output-limit",
        target="fixture",
        mode="dry_run",
        planned_steps=[{"id": "large", "type": "internal", "action": "large"}],
        correlation_id="corr",
        policy_enforcement={
            "binding": _binding(),
            "controls": {
                "max_output_bytes": 128,
                "receipt_required": True,
                "output_digest_required": True,
            },
        },
    )

    assert result.success is False
    assert secret_marker not in repr(result.as_dict())
    output = result.step_results["large"]["output"]
    assert output["output_truncated"]["record"] is True
    assert output["output_digests"]["record"].startswith("sha256:")


def test_output_limit_rolls_back_oversized_internal_state_delta() -> None:
    sensitive_value = "private-state-" * 200

    def mutate_state(context):
        context.shared_state["profile_result"] = sensitive_value
        return {"status": "recorded"}

    result = WorkflowRunner(
        StepExecutor(internal_handlers={"mutate": mutate_state})
    ).run(
        operation_id="op-state-limit",
        target="fixture",
        mode="dry_run",
        planned_steps=[{"id": "mutate", "type": "internal", "action": "mutate"}],
        correlation_id="corr",
        policy_enforcement={
            "binding": _binding(),
            "controls": {
                "max_output_bytes": 128,
                "receipt_required": True,
                "output_digest_required": True,
            },
        },
    )

    assert result.success is False
    assert "profile_result" not in result.shared_state
    assert sensitive_value not in repr(result.as_dict())


def test_connector_output_limit_survives_step_and_receipt_controls() -> None:
    runtime = LocalShellReadonlyRuntime(
        connector_name="bounded_probe",
        config={
            "max_output_bytes": 128,
            "allowlist": [
                {
                    "action": "flood",
                    "command": "/usr/bin/env",
                    "args": [
                        sys.executable,
                        "-c",
                        "import os; os.write(1, b'x' * (8 * 1024 * 1024))",
                    ],
                }
            ],
        },
    )
    runner = WorkflowRunner(
        StepExecutor(connector_dispatcher=ConnectorDispatcher(runtime))
    )

    result = runner.run(
        operation_id="op-bounded-connector-output",
        target="fixture",
        mode="dry_run",
        planned_steps=[
            {
                "id": "flood",
                "type": "connector",
                "connector": "bounded_probe",
                "action": "flood",
            }
        ],
        correlation_id="corr",
        policy_enforcement={
            "controls": {
                "max_output_bytes": 128,
                "output_digest_required": True,
            }
        },
    )

    assert result.success is False
    assert result.error_class == "output_limit_exceeded"
    step_output = result.step_results["flood"]["output"]
    assert step_output["error_class"] == "output_limit_exceeded"
    assert step_output["output_limit_exceeded"] is True
    assert step_output["output_sizes"]["stdout_bytes"] > 128
    assert step_output["output_sizes"]["total_bytes"] > 128
    assert step_output["output_digests"]["stdout"].startswith("sha256:")
    assert step_output["output_digests"]["stderr"].startswith("sha256:")
    assert "stdout" not in step_output
    assert "stderr" not in step_output
    serialized = json.dumps(
        step_output,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    envelope = step_output["overflow_evidence_envelope"]
    assert envelope["schema"] == "rexecop.output_limit_evidence.v0.1"
    assert envelope["evidence_bytes"] == len(serialized)
    assert envelope["evidence_bytes"] <= envelope["max_bytes"] == 2048
    assert b"x" * 128 not in serialized

    receipt = result.shared_state["execution_receipt"]
    assert receipt["success"] is False
    assert receipt["error_class"] == "output_limit_exceeded"
    step_receipt = receipt["step_receipts"][0]
    assert step_receipt["error_class"] == "output_limit_exceeded"
    assert step_receipt["output_digest_refs"]["stdout"].startswith("sha256:")
    assert step_receipt["output_digest_refs"]["stderr"].startswith("sha256:")


def test_tiny_connector_overflow_always_uses_separate_evidence_envelope() -> None:
    runtime = LocalShellReadonlyRuntime(
        connector_name="bounded_probe",
        config={
            "max_output_bytes": 1,
            "allowlist": [
                {
                    "action": "flood",
                    "command": "/usr/bin/env",
                    "args": [
                        sys.executable,
                        "-c",
                        "import os; os.write(1, b' \\xff')",
                    ],
                }
            ],
        },
    )

    result = WorkflowRunner(
        StepExecutor(connector_dispatcher=ConnectorDispatcher(runtime))
    ).run(
        operation_id="op-tiny-bounded-connector-output",
        target="fixture",
        mode="dry_run",
        planned_steps=[
            {
                "id": "flood",
                "type": "connector",
                "connector": "bounded_probe",
                "action": "flood",
            }
        ],
        correlation_id="corr",
        policy_enforcement={
            "controls": {
                "max_output_bytes": 65536,
                "output_digest_required": True,
            }
        },
    )

    assert result.success is False
    assert result.error == "connector output limit exceeded"
    output = result.step_results["flood"]["output"]
    assert set(output) == {
        "error_class",
        "output_limit_exceeded",
        "output_digests",
        "output_truncated",
        "output_sizes",
        "max_output_bytes",
        "overflow_evidence_envelope",
    }
    assert output["max_output_bytes"] == 1
    assert output["output_sizes"]["stdout_bytes"] == 2
    assert output["output_sizes"]["total_bytes"] == 2
    serialized = json.dumps(
        output,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    envelope = output["overflow_evidence_envelope"]
    assert envelope["evidence_bytes"] == len(serialized)
    assert 1 < envelope["evidence_bytes"] <= envelope["max_bytes"] == 2048
    assert not {
        "stdout",
        "stderr",
        "remote_command",
        "returncode",
        "error",
        "data",
        "connector",
        "action",
        "success",
    }.intersection(output)
    receipt = result.shared_state["execution_receipt"]
    assert receipt["enforcement"]["resource_limits"]["max_output_bytes"] == 65536
    assert receipt["step_receipts"][0]["error_class"] == "output_limit_exceeded"


def _synthetic_overflow_data() -> dict[str, object]:
    return {
        "stdout": "raw-marker-must-not-survive",
        "stderr": "raw-error-must-not-survive",
        "remote_command": "raw-command-must-not-survive",
        "returncode": 0,
        "error_class": "output_limit_exceeded",
        "output_limit_exceeded": True,
        "max_output_bytes": 1,
        "output_digests": {
            "stdout": "sha256:" + "0" * 64,
            "stderr": "sha256:" + "0" * 64,
        },
        "output_sizes": {
            "stdout_bytes": 2,
            "stderr_bytes": 0,
            "total_bytes": 2,
        },
        "output_truncated": {"stdout": True, "stderr": False},
    }


def _run_synthetic_overflow(
    data: dict[str, object],
    *,
    active_max_output_bytes: int = 65536,
) -> WorkflowRunResult:
    class SyntheticRuntime:
        def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                data=data,
                error="raw-response-error-must-not-survive",
            )

    return WorkflowRunner(
        StepExecutor(connector_dispatcher=ConnectorDispatcher(SyntheticRuntime()))
    ).run(
        operation_id="op-synthetic-overflow",
        target="fixture",
        mode="dry_run",
        planned_steps=[
            {
                "id": "probe",
                "type": "connector",
                "connector": "bounded_probe",
                "action": "probe",
            }
        ],
        correlation_id="corr",
        policy_enforcement={
            "controls": {"max_output_bytes": active_max_output_bytes}
        },
    )


def _run_synthetic_overflow_with_attempt(
    data: dict[str, object],
) -> tuple[
    WorkflowRunResult,
    dict[str, Any],
    list[str],
    list[tuple[str, dict[str, Any]]],
]:
    class SyntheticRuntime:
        def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                data=data,
                error="raw-response-error-must-not-survive",
            )

    attempt: dict[str, Any] = {
        "attempt_id": "attempt-overflow",
        "status": "started",
    }
    ordering: list[str] = []
    finished: list[tuple[str, dict[str, Any]]] = []

    def start_attempt(
        _context: StepExecutionContext,
        _spec: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return attempt

    def finish_attempt(
        active_attempt: dict[str, Any],
        status: str,
        result: StepExecutionResult | None,
    ) -> None:
        assert result is not None
        payload = result.as_dict()
        active_attempt["status"] = status
        active_attempt["result_digest"] = "sha256:" + canonical_digest(payload)
        finished.append((status, payload))
        ordering.append("finish")

    def bind_receipt(
        active_attempt: dict[str, Any],
        result: StepExecutionResult,
    ) -> StepExecutionResult:
        assert active_attempt["status"] == "failed"
        ordering.append("receipt")
        return replace(
            result,
            runtime_receipt_binding={
                "attempt_id": active_attempt["attempt_id"],
                "attempt_status": active_attempt["status"],
            },
        )

    result = WorkflowRunner(
        StepExecutor(
            connector_dispatcher=ConnectorDispatcher(SyntheticRuntime()),
            attempt_start_handler=start_attempt,
            attempt_finish_handler=finish_attempt,
            attempt_receipt_handler=bind_receipt,
        )
    ).run(
        operation_id="op-synthetic-overflow-attempt",
        target="fixture",
        mode="dry_run",
        planned_steps=[
            {
                "id": "probe",
                "type": "connector",
                "connector": "bounded_probe",
                "action": "probe",
            }
        ],
        correlation_id="corr",
        policy_enforcement={"controls": {"max_output_bytes": 65536}},
    )
    return result, attempt, ordering, finished


def test_attempt_finish_canonicalizes_only_projected_valid_overflow() -> None:
    result, attempt, ordering, finished = _run_synthetic_overflow_with_attempt(
        _synthetic_overflow_data()
    )

    assert ordering == ["finish", "receipt"]
    assert attempt["status"] == "failed"
    assert str(attempt["result_digest"]).startswith("sha256:")
    assert len(finished) == 1
    status, payload = finished[0]
    assert status == "failed"
    output = payload["output"]
    assert set(output) == {
        "error_class",
        "output_limit_exceeded",
        "output_digests",
        "output_truncated",
        "output_sizes",
        "max_output_bytes",
        "overflow_evidence_envelope",
    }
    assert "raw-marker-must-not-survive" not in repr(payload)
    assert "raw-response-error-must-not-survive" not in repr(payload)
    assert "internal_error" not in repr(payload)
    binding = result.step_results["probe"]["runtime_receipt_binding"]
    assert binding == {
        "attempt_id": "attempt-overflow",
        "attempt_status": "failed",
    }


def test_attempt_finish_canonicalizes_only_bounded_recursive_overflow_failure() -> None:
    data = _synthetic_overflow_data()
    cycle: list[object] = []
    cycle.append(cycle)
    data["attacker_marker"] = cycle

    result, attempt, ordering, finished = _run_synthetic_overflow_with_attempt(data)

    assert result.success is False
    assert result.error_class == "validation_failed"
    assert ordering == ["finish", "receipt"]
    assert attempt["status"] == "failed"
    assert str(attempt["result_digest"]).startswith("sha256:")
    assert len(finished) == 1
    status, payload = finished[0]
    assert status == "failed"
    assert set(payload["output"]) == {
        "error_class",
        "overflow_evidence_envelope",
    }
    assert payload["output"]["error_class"] == "validation_failed"
    assert "attacker_marker" not in repr(payload)
    assert "raw-marker-must-not-survive" not in repr(payload)
    assert "internal_error" not in repr(payload)
    binding = result.step_results["probe"]["runtime_receipt_binding"]
    assert binding["attempt_status"] == "failed"


def test_side_effectful_success_finishes_only_after_output_and_receipt_postconditions(
    allow_lab_mutation_runtime_test: None,
) -> None:
    class SuccessfulMutationRuntime:
        calls = 0

        def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
            self.calls += 1
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=True,
                data={"status": "changed"},
            )

    runtime = SuccessfulMutationRuntime()
    attempt: dict[str, Any] = {
        "attempt_id": "attempt-side-effect-success",
        "side_effectful": True,
        "status": "started",
    }
    ordering: list[str] = []

    def bind_receipt(
        active_attempt: dict[str, Any],
        result: StepExecutionResult,
    ) -> StepExecutionResult:
        assert active_attempt["status"] == "started"
        assert result.output["output_digests"]["record"].startswith("sha256:")
        ordering.append("receipt")
        return replace(
            result,
            runtime_receipt_binding={"attempt_id": active_attempt["attempt_id"]},
        )

    def finish_attempt(
        active_attempt: dict[str, Any],
        status: str,
        result: StepExecutionResult | None,
    ) -> None:
        assert result is not None
        assert result.runtime_receipt_binding["attempt_id"] == active_attempt["attempt_id"]
        active_attempt["status"] = status
        ordering.append("finish")

    result = WorkflowRunner(
        StepExecutor(
            connector_dispatcher=ConnectorDispatcher(runtime),
            attempt_start_handler=lambda _context, _spec: attempt,
            attempt_finish_handler=finish_attempt,
            attempt_receipt_handler=bind_receipt,
        )
    ).run(
        operation_id="op-side-effect-success",
        target="fixture",
        mode="apply",
        planned_steps=[
            {
                "id": "change",
                "type": "connector",
                "connector": "fixture",
                "action": "change",
            }
        ],
        correlation_id="corr",
    )

    assert result.success is True
    assert runtime.calls == 1
    assert ordering == ["receipt", "finish"]
    assert attempt["status"] == "completed"


def test_side_effectful_success_with_output_uncertainty_is_indeterminate(
    allow_lab_mutation_runtime_test: None,
) -> None:
    raw_marker = "raw-marker-must-not-survive-" * 200

    class OversizedMutationRuntime:
        calls = 0

        def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
            self.calls += 1
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=True,
                data={"value": raw_marker},
            )

    runtime = OversizedMutationRuntime()
    attempt: dict[str, Any] = {
        "attempt_id": "attempt-side-effect-output-uncertain",
        "side_effectful": True,
        "status": "started",
    }
    ordering: list[str] = []
    finished: list[tuple[str, dict[str, Any]]] = []

    def bind_receipt(
        active_attempt: dict[str, Any],
        result: StepExecutionResult,
    ) -> StepExecutionResult:
        assert active_attempt["status"] == "started"
        assert result.success is False
        ordering.append("receipt")
        return result

    def finish_attempt(
        active_attempt: dict[str, Any],
        status: str,
        result: StepExecutionResult | None,
    ) -> None:
        assert result is not None
        active_attempt["status"] = status
        finished.append((status, result.as_dict()))
        ordering.append("finish")

    result = WorkflowRunner(
        StepExecutor(
            connector_dispatcher=ConnectorDispatcher(runtime),
            attempt_start_handler=lambda _context, _spec: attempt,
            attempt_finish_handler=finish_attempt,
            attempt_receipt_handler=bind_receipt,
        )
    ).run(
        operation_id="op-side-effect-output-uncertain",
        target="fixture",
        mode="apply",
        planned_steps=[
            {
                "id": "change",
                "type": "connector",
                "connector": "fixture",
                "action": "change",
            }
        ],
        correlation_id="corr",
        policy_enforcement={"controls": {"max_output_bytes": 128}},
    )

    assert result.success is False
    assert result.error_class == "outcome_indeterminate"
    assert runtime.calls == 1
    assert ordering == ["receipt", "finish"]
    assert attempt["status"] == "indeterminate"
    assert finished[0][0] == "indeterminate"
    assert finished[0][1]["output"]["error_class"] == "outcome_indeterminate"
    assert raw_marker not in repr(result.as_dict())


@pytest.mark.parametrize(
    "tamper",
    [
        "count_type",
        "limit_type",
        "total_mismatch",
        "no_truncation",
        "untruncated_oversized_stream",
        "limit_above_policy",
        "bad_digest",
    ],
)
def test_tampered_connector_overflow_evidence_fails_closed(tamper: str) -> None:
    data = _synthetic_overflow_data()
    if tamper == "count_type":
        data["output_sizes"]["stdout_bytes"] = "2"  # type: ignore[index]
    elif tamper == "limit_type":
        data["max_output_bytes"] = 1.0
    elif tamper == "total_mismatch":
        data["output_sizes"]["total_bytes"] = 3  # type: ignore[index]
    elif tamper == "no_truncation":
        data["output_truncated"] = {"stdout": False, "stderr": False}
    elif tamper == "untruncated_oversized_stream":
        data["output_sizes"] = {
            "stdout_bytes": 2,
            "stderr_bytes": 1,
            "total_bytes": 3,
        }
        data["output_truncated"] = {"stdout": False, "stderr": True}
    elif tamper == "limit_above_policy":
        data["max_output_bytes"] = 65537
        data["output_sizes"] = {
            "stdout_bytes": 65538,
            "stderr_bytes": 0,
            "total_bytes": 65538,
        }
    else:
        data["output_digests"]["stdout"] = "sha256:not-a-digest"  # type: ignore[index]

    result = _run_synthetic_overflow(data)

    assert result.success is False
    assert result.error_class == "validation_failed"
    assert result.error == "execution output failed overflow evidence validation"
    output = result.step_results["probe"]["output"]
    assert output["error_class"] == "validation_failed"
    assert "raw-marker-must-not-survive" not in repr(result.as_dict())
    envelope = output["overflow_evidence_envelope"]
    serialized = json.dumps(
        output,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert envelope["evidence_bytes"] == len(serialized)
    assert envelope["evidence_bytes"] <= envelope["max_bytes"] == 2048
    assert "output_digests" not in output
    assert "output_sizes" not in output


@pytest.mark.parametrize(
    "pathology",
    ["pathological_error_class", "huge_arbitrary_int", "huge_count", "cycle"],
)
def test_pathological_overflow_evidence_returns_fixed_bounded_failure(
    pathology: str,
) -> None:
    data = _synthetic_overflow_data()

    class PathologicalText:
        def __str__(self) -> str:
            raise RuntimeError("attacker text must not escape")

    if pathology == "pathological_error_class":
        data["error_class"] = PathologicalText()
    elif pathology == "huge_arbitrary_int":
        data["attacker_marker"] = 10**5000
    elif pathology == "huge_count":
        huge_count = 10**5000
        data["output_sizes"] = {
            "stdout_bytes": huge_count,
            "stderr_bytes": 0,
            "total_bytes": huge_count,
        }
    else:
        cycle: list[object] = []
        cycle.append(cycle)
        data["attacker_marker"] = cycle

    result = _run_synthetic_overflow(data)

    assert result.success is False
    assert result.error_class == "validation_failed"
    assert result.error == "execution output failed overflow evidence validation"
    output = result.step_results["probe"]["output"]
    assert set(output) == {"error_class", "overflow_evidence_envelope"}
    assert output["error_class"] == "validation_failed"
    assert "output_digests" not in output
    assert "output_sizes" not in output
    serialized = json.dumps(
        output,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    envelope = output["overflow_evidence_envelope"]
    assert envelope["evidence_bytes"] == len(serialized)
    assert envelope["evidence_bytes"] <= envelope["max_bytes"] == 2048
    assert "attacker" not in serialized.decode("ascii")
    assert "internal_error" not in serialized.decode("ascii")


def test_connector_overflow_limit_may_be_smaller_than_active_policy() -> None:
    result = _run_synthetic_overflow(
        _synthetic_overflow_data(),
        active_max_output_bytes=8,
    )

    assert result.success is False
    assert result.error_class == "output_limit_exceeded"
    output = result.step_results["probe"]["output"]
    assert output["max_output_bytes"] == 1
    assert output["output_sizes"]["total_bytes"] == 2
    assert output["output_digests"]["record"].startswith("sha256:")
    assert output["output_sizes"]["record_bytes"] > 0


@pytest.mark.parametrize(
    ("stdout_truncated", "stderr_truncated"),
    [(True, False), (False, True), (True, True)],
)
def test_combined_stream_allocation_accepts_exact_truncation_evidence(
    stdout_truncated: bool,
    stderr_truncated: bool,
) -> None:
    data = _synthetic_overflow_data()
    data["max_output_bytes"] = 3
    data["output_sizes"] = {
        "stdout_bytes": 2,
        "stderr_bytes": 2,
        "total_bytes": 4,
    }
    data["output_truncated"] = {
        "stdout": stdout_truncated,
        "stderr": stderr_truncated,
    }

    result = _run_synthetic_overflow(data, active_max_output_bytes=3)

    assert result.success is False
    assert result.error_class == "output_limit_exceeded"
    output = result.step_results["probe"]["output"]
    assert output["output_sizes"]["total_bytes"] == 4
    assert output["output_truncated"] == {
        "stdout": stdout_truncated,
        "stderr": stderr_truncated,
        "record": True,
    }


def test_policy_timeout_is_tighter_than_connector_configuration() -> None:
    runtime = LocalShellReadonlyRuntime(
        connector_name="host",
        config={
            "timeout_seconds": 30,
            "allowlist": [{"action": "uptime", "command": "uptime", "args": []}],
        },
    )

    class Completed:
        returncode = 0
        stdout = "up"
        stderr = ""

    with patch(
        "rexecop.connectors.local_shell.subprocess.run",
        return_value=Completed(),
    ) as run:
        response = runtime.invoke(
            ConnectorRequest(
                connector="host",
                action="uptime",
                target="fixture",
                mode="dry_run",
                metadata={"execution_controls": {"timeout_seconds": 5}},
            )
        )

    assert response.success is True
    assert run.call_args.kwargs["timeout"] == 5


def test_execution_request_and_receipt_digests_detect_drift() -> None:
    request = execution_request_from_workflow(
        operation_id="op-digests",
        target="fixture",
        mode="dry_run",
        planned_steps=[{"id": "ok", "type": "internal", "action": "ok"}],
    )
    receipt = execution_receipt_from_results(
        request=request,
        success=True,
        executed_steps=["ok"],
        step_results={"ok": {"success": True, "output": {"status": "ok"}}},
    )

    assert request.schema_version == "v0.2"
    assert receipt.schema_version == "v0.2"
    assert receipt.request_digest == execution_request_digest(request)
    assert receipt.receipt_digest == execution_receipt_digest(receipt)
    assert execution_request_digest(replace(request, target_ref="drifted")) != (
        receipt.request_digest
    )
    assert execution_receipt_digest(replace(receipt, success=False)) != (
        receipt.receipt_digest
    )


def _binding() -> dict[str, str]:
    digest = "sha256:" + "a" * 64
    return {
        "schema_version": "v0.1",
        "enforcement_plan_id": "policy-enforcement:test",
        "enforcement_plan_digest": digest,
        "admission_id": "policy-admission:test",
        "admission_digest": digest,
        "policy_pack_id": "test",
        "policy_pack_version": "1",
        "policy_pack_digest": digest,
        "verdict_id": "verdict:test",
        "verdict_digest": digest,
    }
