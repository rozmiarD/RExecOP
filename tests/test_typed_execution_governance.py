from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from rexecop.connectors.capability_descriptor import compile_connector_capability_descriptor
from rexecop.connectors.errors import POLICY_DENIED
from rexecop.connectors.runtime import ConnectorDispatcher
from rexecop.connectors.static_fixture import StaticFixtureRuntime
from rexecop.errors import RExecOpValidationError
from rexecop.execution.executor import StepExecutor
from rexecop.execution.govengine_governance import (
    TYPED_EXECUTION_GOVERNANCE_BUNDLE_SCHEMA,
    TYPED_EXECUTION_STACK_COMPATIBILITY_SCHEMA,
    build_typed_execution_governance_request,
    build_typed_execution_stack_compatibility_request,
    enforce_typed_execution_governance,
    evaluate_typed_execution_governance,
    evaluate_typed_execution_stack_compatibility,
    typed_execution_governance_overlay,
)
from rexecop.execution.typed_spec import compile_step_execution_spec
from rexecop.profile.loader import load_profile
from rexecop.runtime.doctor import run_runtime_doctor
from rexecop.workflow.runner import WorkflowRunner

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PROFILE = ROOT / "examples/profiles/runtime-fixture/profile.yaml"
FIXTURE_ENV = ROOT / "examples/environments/runtime-fixture.example.yaml"


def _fixture_spec(*, mode: str = "dry_run") -> dict:
    env = yaml.safe_load(FIXTURE_ENV.read_text(encoding="utf-8"))
    profile = load_profile(FIXTURE_PROFILE)
    step = {
        "id": "inspect_state",
        "type": "connector",
        "connector": "fixture_source",
        "action": "read_fixture_state",
    }
    return compile_step_execution_spec(
        step=step,
        profile=profile,
        connector_config=env["environment"]["connectors"]["fixture_source"],
        mode=mode,
    )


def test_build_typed_execution_governance_request_from_fixture_spec() -> None:
    spec = _fixture_spec()
    request = build_typed_execution_governance_request(
        spec=spec,
        operation_id="op-1",
        mode="dry_run",
    )

    assert request["schema_version"] == "v0.1"
    assert request["step_execution_spec_digest"] == spec["digest"]
    assert request["capability_descriptor_digest"] == spec["capability_descriptor"]["digest"]
    assert request["payload_digest"] == spec["payload"]["action_digest"]
    assert request["side_effect_class"] == "read_only"
    assert request["allowed_network_egress"] == ["no_network"]


def test_evaluate_typed_execution_governance_passes_for_readonly_fixture() -> None:
    spec = _fixture_spec()
    result = evaluate_typed_execution_governance(
        spec=spec,
        operation_id="op-readonly",
        mode="dry_run",
    )

    assert result["status"] == "passed"
    assert result["schema"] == TYPED_EXECUTION_GOVERNANCE_BUNDLE_SCHEMA
    assert result["governance"]["status"] == "passed"
    assert result["compatibility"]["status"] == "passed"


def test_blocked_raw_shell_via_governance_request() -> None:
    with pytest.raises(RExecOpValidationError, match="raw shell backend blocked"):
        compile_connector_capability_descriptor(
            connector="host",
            backend_class="shell",
            connector_config={"enabled": True, "backend": "shell"},
            mode="dry_run",
        )
    spec = _fixture_spec()
    capability = dict(spec["capability_descriptor"])
    capability.update(
        {
            "backend_class": "shell",
            "egress_class": "local_subprocess",
            "network_boundary": {"egress": "local_subprocess", "host_declared": False},
            "declared_capability_descriptors": [],
        }
    )
    spec = {**spec, "backend_class": "shell", "capability_descriptor": capability}
    result = evaluate_typed_execution_governance(
        spec=spec,
        operation_id="op-shell",
        mode="dry_run",
        allowed_network_egress=["local_subprocess"],
    )

    assert result["status"] == "blocked"
    assert "raw_shell_backend_blocked" in result["compatibility"]["blockers"]


def test_blocked_unsupported_backend_via_governance_request() -> None:
    spec = _fixture_spec()
    capability = dict(spec["capability_descriptor"])
    capability.update(
        {
            "backend_class": "undeclared_plugin",
            "egress_class": "plugin_undeclared",
            "network_boundary": {"egress": "plugin_undeclared", "host_declared": False},
            "declared_capability_descriptors": ["connector.plugin.undeclared_plugin"],
            "certification_tier": "plugin",
            "identity_class": "plugin_declared",
        }
    )
    spec = {**spec, "backend_class": "undeclared_plugin", "capability_descriptor": capability}
    result = evaluate_typed_execution_governance(
        spec=spec,
        operation_id="op-unsupported",
        mode="dry_run",
        allowed_network_egress=["plugin_undeclared"],
    )

    assert result["status"] == "blocked"
    assert "unsupported_backend_class" in result["compatibility"]["blockers"]


def test_blocked_missing_output_digest_ref() -> None:
    spec = _fixture_spec()
    result = evaluate_typed_execution_governance(
        spec=spec,
        operation_id="op-output",
        mode="dry_run",
        evidence_requirements={
            "receipt_required": True,
            "output_digest_required": True,
        },
    )

    assert result["status"] == "blocked"
    assert "missing_output_digest_ref" in result["governance"]["blockers"]


def test_blocked_network_boundary_mismatch() -> None:
    spec = _fixture_spec()
    result = evaluate_typed_execution_governance(
        spec=spec,
        operation_id="op-network",
        mode="dry_run",
        allowed_network_egress=["outbound_http"],
    )

    assert result["status"] == "blocked"
    assert "network_boundary_mismatch" in result["compatibility"]["blockers"]


def test_blocked_mutation_requiring_approval() -> None:
    env = yaml.safe_load(FIXTURE_ENV.read_text(encoding="utf-8"))
    profile = load_profile(FIXTURE_PROFILE)
    connector_config = dict(env["environment"]["connectors"]["fixture_source"])
    connector_config["actions"] = {
        "apply_fixture_change": {"mutating": True, "data": {"changed": True}}
    }
    step = {
        "id": "apply_change",
        "type": "connector",
        "connector": "fixture_source",
        "action": "apply_fixture_change",
    }
    spec = compile_step_execution_spec(
        step=step,
        profile=profile,
        connector_config=connector_config,
        mode="apply",
    )
    result = evaluate_typed_execution_governance(
        spec=spec,
        operation_id="op-mutation",
        mode="apply",
        evidence_requirements={"receipt_required": True},
    )

    assert result["status"] == "blocked"
    assert "mutation_requires_approval_evidence" in result["governance"]["blockers"]


def test_workflow_runner_enforces_typed_execution_governance_before_backend_io() -> None:
    runtime = StaticFixtureRuntime(
        connector_name="fixture_source",
        mutating_allowed=False,
        config={
            "fixture_only": True,
            "actions": {
                "read_fixture_state": {"data": {"observed": True}},
            },
        },
    )
    executor = StepExecutor(connector_dispatcher=ConnectorDispatcher(runtime))
    env = yaml.safe_load(FIXTURE_ENV.read_text(encoding="utf-8"))
    shared_state = {
        "execution_context": {
            "profile_root": str(FIXTURE_PROFILE),
            "connectors": env["environment"]["connectors"],
        },
        "typed_execution_governance": {
            "allowed_network_egress": ["outbound_http"],
        },
    }

    with patch("rexecop.connectors.http_api.urllib.request.urlopen") as backend:
        result = WorkflowRunner(executor).run(
            operation_id="op-governance-blocked",
            target="fixture-target",
            mode="dry_run",
            planned_steps=[
                {
                    "id": "inspect_state",
                    "type": "connector",
                    "connector": "fixture_source",
                    "action": "read_fixture_state",
                }
            ],
            correlation_id="corr-governance",
            shared_state=shared_state,
        )

    backend.assert_not_called()
    assert not result.success
    assert result.error_class == POLICY_DENIED
    assert "typed_execution_admissions" in result.shared_state
    assert result.shared_state["typed_execution_admissions"]["inspect_state"]["allowed"] is False


def test_workflow_runner_allows_readonly_fixture_with_matching_governance() -> None:
    runtime = StaticFixtureRuntime(
        connector_name="fixture_source",
        mutating_allowed=False,
        config={
            "fixture_only": True,
            "actions": {
                "read_fixture_state": {"data": {"observed": True}},
            },
        },
    )
    executor = StepExecutor(connector_dispatcher=ConnectorDispatcher(runtime))
    env = yaml.safe_load(FIXTURE_ENV.read_text(encoding="utf-8"))
    shared_state = {
        "execution_context": {
            "profile_root": str(FIXTURE_PROFILE),
            "connectors": env["environment"]["connectors"],
        }
    }

    with patch("rexecop.connectors.http_api.urllib.request.urlopen") as backend:
        result = WorkflowRunner(executor).run(
            operation_id="op-governance-allowed",
            target="fixture-target",
            mode="dry_run",
            planned_steps=[
                {
                    "id": "inspect_state",
                    "type": "connector",
                    "connector": "fixture_source",
                    "action": "read_fixture_state",
                }
            ],
            correlation_id="corr-governance-ok",
            shared_state=shared_state,
        )

    backend.assert_not_called()
    assert result.success
    admission = result.shared_state["typed_execution_admissions"]["inspect_state"]
    assert admission["allowed"] is True
    assert admission["reason_code"] == "typed_execution_admission_allowed"


def test_typed_execution_stack_compatibility_passes_for_builtin_backends() -> None:
    result = evaluate_typed_execution_stack_compatibility()

    assert result["status"] == "passed"
    assert result["schema"] == TYPED_EXECUTION_STACK_COMPATIBILITY_SCHEMA
    assert "http_api" in result["supported_backends"]
    assert "static_fixture" in result["supported_backends"]
    request = build_typed_execution_stack_compatibility_request()
    assert request["backend_descriptors"]


def test_runtime_doctor_includes_typed_execution_stack_compatibility(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "runtime_manifest.json").write_text("{}\n", encoding="utf-8")
    for relative in (
        "operations",
        "plans",
        "evidence",
        "receipts",
        "sclite",
        "approvals",
        "queue",
    ):
        (root / relative).mkdir(parents=True)
    (root / "queue" / "run_now.json").write_text("[]\n", encoding="utf-8")

    report = run_runtime_doctor(root)

    check = next(
        item
        for item in report["checks"]
        if item["id"] == "typed_execution_stack_compatibility"
    )
    assert check["status"] == "passed"


def test_policy_enforcement_controls_flow_into_typed_execution_overlay() -> None:
    operation = {
        "id": "op-policy-overlay",
        "metadata": {
            "policy_enforcement": {
                "admission_digest": "sha256:" + "a" * 64,
                "plan": {
                    "controls": {
                        "receipt_required": True,
                        "output_digest_required": True,
                        "no_raw_shell": True,
                        "allowed_network_egress": ["no_network"],
                        "typed_execution_control_ids": [
                            "output_digest_required",
                            "network_boundary_match",
                        ],
                    }
                },
            }
        },
    }
    overlay = typed_execution_governance_overlay(operation)

    assert overlay["evidence_requirements"]["output_digest_required"] is True
    assert overlay["allowed_network_egress"] == ["no_network"]
    assert overlay["no_raw_shell"] is True


def test_policy_output_digest_required_blocks_without_ref_in_overlay() -> None:
    spec = _fixture_spec()
    shared_state = {
        "typed_execution_governance": typed_execution_governance_overlay(
            {
                "metadata": {
                    "policy_enforcement": {
                        "plan": {
                            "controls": {
                                "receipt_required": True,
                                "output_digest_required": True,
                                "typed_execution_control_ids": ["output_digest_required"],
                            }
                        }
                    }
                }
            }
        )
    }
    result = evaluate_typed_execution_governance(
        spec=spec,
        operation_id="op-policy-digest",
        mode="dry_run",
        shared_state=shared_state,
    )

    assert result["status"] == "blocked"
    assert "missing_output_digest_ref" in result["governance"]["blockers"]


def test_enforce_typed_execution_governance_stores_admission_record() -> None:
    spec = _fixture_spec()
    shared_state: dict = {}
    admission = enforce_typed_execution_governance(
        spec=spec,
        operation_id="op-store",
        mode="dry_run",
        shared_state=shared_state,
    )

    assert admission["allowed"] is True
    assert shared_state["typed_execution_admissions"]["inspect_state"]["allowed"] is True