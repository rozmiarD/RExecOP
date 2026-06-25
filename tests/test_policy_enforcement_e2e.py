from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.local_shell import LocalShellReadonlyRuntime
from rexecop.errors import RExecOpValidationError
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
from rexecop.workflow.runner import WorkflowRunner

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def _policy_environment(tmp_path: Path, *, max_steps: int = 20) -> Path:
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
                "constraints": [
                    {
                        "constraint_id": "steps",
                        "kind": "max_steps",
                        "value": max_steps,
                    },
                    {
                        "constraint_id": "output",
                        "kind": "output_limit",
                        "value": 8192,
                    },
                ],
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
