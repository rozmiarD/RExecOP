from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from govengine import validate_supported_contract_version
from sclite.artifacts import artifact_sha256

from rexecop.errors import RExecOpValidationError
from rexecop.evidence.public_projection import (
    project_public_payload,
    sanitize_for_public_surface,
)
from rexecop.execution.govengine_governance import (
    build_typed_execution_governance_request,
    evaluate_typed_execution_governance,
)
from rexecop.execution.typed_spec import compile_step_execution_spec
from rexecop.profile.loader import load_profile
from rexecop.runtime.contract_compatibility import (
    COMPATIBILITY_POLICY,
    validate_rexecop_projection_version,
)
from rexecop.runtime_ops.idempotency import (
    IDEMPOTENCY_SCHEMA,
    canonical_idempotency_digest,
    plan_idempotency_key,
    reaction_child_plan_key,
    start_idempotency_key,
)
from rexecop.runtime_ops.recovery import start_is_idempotent
from rexecop.storage.file_store import FileStore

pytestmark = pytest.mark.invariant

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PROFILE = ROOT / "examples/profiles/runtime-fixture/profile.yaml"
FIXTURE_ENV = ROOT / "examples/environments/runtime-fixture.example.yaml"


def _fixture_spec(*, mode: str = "dry_run") -> dict:
    import yaml

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


# --- canonical_digest_normalization ---


def test_artifact_sha256_is_key_order_independent() -> None:
    first = {"b": 2, "a": 1, "nested": {"z": 9, "y": 8}}
    second = {"nested": {"y": 8, "z": 9}, "a": 1, "b": 2}
    assert artifact_sha256(first) == artifact_sha256(second)


def test_idempotency_digest_uses_canonical_json() -> None:
    payload = {"kind": "plan", "mode": "dry_run", "target": "fixture-target"}
    shuffled = {"target": "fixture-target", "kind": "plan", "mode": "dry_run"}
    assert canonical_idempotency_digest(payload) == canonical_idempotency_digest(shuffled)


def test_public_projection_digest_is_stable() -> None:
    payload = {"output": {"stdout": "diagnostic"}}
    first = project_public_payload(payload, allowlist=frozenset())
    second = project_public_payload(deepcopy(payload), allowlist=frozenset())
    assert first["output"]["stdout"] == second["output"]["stdout"]


# --- unknown_major_fail_closed ---


def test_compatibility_policy_is_unknown_major_fail_closed() -> None:
    assert COMPATIBILITY_POLICY == "unknown_major_fail_closed"


@pytest.mark.parametrize(
    ("surface_id", "version"),
    [
        ("execution_request", "v9.0"),
        ("execution_receipt", "v2.0"),
        ("step_execution_spec", "v9.0"),
    ],
)
def test_runtime_projection_unknown_major_fail_closed(
    surface_id: str,
    version: str,
) -> None:
    with pytest.raises(
        RExecOpValidationError,
        match="unsupported_runtime_projection_major_version",
    ):
        validate_rexecop_projection_version(surface_id, version)


@pytest.mark.parametrize(
    "contract_id",
    ["typed_execution_governance_request", "typed_execution_governance_bundle"],
)
def test_govengine_contract_unknown_major_fail_closed(contract_id: str) -> None:
    with pytest.raises(Exception):
        validate_supported_contract_version(contract_id, "v9.0")


# --- policy_admission_spec_binding ---


def test_typed_execution_governance_binds_spec_and_capability_digests() -> None:
    spec = _fixture_spec()
    request = build_typed_execution_governance_request(
        spec=spec,
        operation_id="op-binding",
        mode="dry_run",
    )
    assert request["step_execution_spec_digest"] == spec["digest"]
    assert request["capability_descriptor_digest"].startswith("sha256:")
    assert request["capability_descriptor_digest"] != spec["capability_descriptor"]["digest"]


def test_typed_execution_binding_blocks_missing_output_digest_when_required() -> None:
    spec = _fixture_spec()
    result = evaluate_typed_execution_governance(
        spec=spec,
        operation_id="op-binding",
        mode="dry_run",
        evidence_requirements={
            "receipt_required": True,
            "output_digest_required": True,
        },
    )
    assert result["status"] == "blocked"
    assert "missing_output_digest_ref" in result["governance"]["blockers"]


# --- public_projection_allowlist ---


def test_public_projection_allowlist_blocks_raw_stdout_by_default() -> None:
    projected = project_public_payload(
        {"output": {"stdout": "host payload", "error_class": "timeout"}},
        allowlist=frozenset(),
    )
    assert projected["output"]["stdout"]["projection"] == "digest_only"
    assert projected["output"]["error_class"] == "timeout"


def test_public_projection_allowlist_then_redaction_masks_secrets() -> None:
    sanitized = sanitize_for_public_surface(
        {"output": {"body_snippet": "token=fixture-secret-value"}},
        allowlist=frozenset({"output.body_snippet"}),
    )
    assert "fixture-secret-value" not in json.dumps(sanitized)


# --- idempotency_replay_recovery ---


def test_plan_and_start_idempotency_keys_are_distinct_and_stable() -> None:
    plan_key = plan_idempotency_key(
        profile="runtime-fixture",
        environment="runtime-fixture",
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    again = plan_idempotency_key(
        profile="runtime-fixture",
        environment="runtime-fixture",
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    start_key = start_idempotency_key("op-123")
    assert plan_key == again
    assert plan_key != start_key
    assert len(plan_key) == 64


def test_idempotency_schema_is_declared_on_keys() -> None:
    assert IDEMPOTENCY_SCHEMA == "rexecop.idempotency.v0.1"


def test_terminal_operation_start_is_idempotent(tmp_path: Path) -> None:
    from rexecop.operation.controller import OperationController
    from rexecop.operation.state import OperationState

    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    operation = controller.plan(
        profile_path=FIXTURE_PROFILE,
        environment_path=FIXTURE_ENV,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    completed = controller.start(operation.id)
    assert completed.state == OperationState.COMPLETED.value
    assert start_is_idempotent(completed) is True


def test_reaction_child_plan_key_is_stable() -> None:
    first = reaction_child_plan_key(reaction_id="reaction-1", child_operation_id="op-1")
    second = reaction_child_plan_key(reaction_id="reaction-1", child_operation_id="op-1")
    different = reaction_child_plan_key(reaction_id="reaction-2", child_operation_id="op-1")
    assert first == second
    assert first != different
    assert len(first) == 64
