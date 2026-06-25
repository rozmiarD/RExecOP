from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.composite_runtime import build_connector_runtime
from rexecop.environment.loader import load_environment
from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.policy.pack import compile_environment_policy_pack
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"
POLICY_ENVIRONMENT = (
    REPO_ROOT / "examples/environments/runtime-fixture.policy.example.yaml"
)
POLICY_PACK_PATH = REPO_ROOT / "examples/policy/rexecop-connectors-default.yaml"


def _policy_pack() -> dict:
    return yaml.safe_load(POLICY_PACK_PATH.read_text())


def test_compile_environment_policy_pack_rejects_conflicts() -> None:
    with pytest.raises(RExecOpValidationError, match="invalid policy_pack"):
        compile_environment_policy_pack(
            {
                "policy_id": "bad",
                "version": "1",
                "rules": [
                    {"rule_id": "a", "effect": "allow", "conditions": {"action.mode": "read"}},
                    {"rule_id": "b", "effect": "deny", "conditions": {"action.mode": "read"}},
                ],
            }
        )


def test_connector_policy_denies_ssh_on_critical_before_backend() -> None:
    pack = compile_environment_policy_pack(_policy_pack())
    runtime = build_connector_runtime(
        connectors={
            "pve_ro": {
                "enabled": True,
                "backend": "ssh_readonly",
                "host": "pve.example.com",
                "user": "ro",
                "allowlist": [{"action": "uptime", "command": "uptime"}],
            }
        },
        profile_root=None,
        mutating_allowed=False,
        policy_pack=pack,
        operation_id="op-policy-1",
        target_criticality="critical",
    )

    with patch("rexecop.connectors.ssh_readonly.subprocess.run") as run_mock:
        response = runtime.invoke(
            ConnectorRequest(
                connector="pve_ro",
                action="uptime",
                target="fixture-target",
                mode="dry_run",
            )
        )

    run_mock.assert_not_called()
    assert response.success is False
    assert response.data["error_class"] == connector_errors.POLICY_DENIED
    assert response.data["policy_reason_code"] == "ssh_denied_on_critical_target"


def test_connector_policy_allows_read_shell_on_critical() -> None:
    pack = compile_environment_policy_pack(_policy_pack())
    runtime = build_connector_runtime(
        connectors={
            "host_probe": {
                "enabled": True,
                "backend": "local_shell_readonly",
                "allowlist": [{"action": "uptime", "command": "uptime"}],
            }
        },
        profile_root=None,
        mutating_allowed=False,
        policy_pack=pack,
        operation_id="op-policy-2",
        target_criticality="critical",
    )

    class Result:
        returncode = 0
        stdout = "up"
        stderr = ""

    with patch("rexecop.connectors.local_shell.subprocess.run", return_value=Result()):
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_probe",
                action="uptime",
                target="fixture-target",
                mode="dry_run",
            )
        )

    assert response.success is True


def test_connector_policy_blocks_unenforced_obligations_before_backend() -> None:
    pack = compile_environment_policy_pack(
        {
            "policy_id": "obligated-read",
            "version": "1",
            "rules": [
                {
                    "rule_id": "allow-read-with-controls",
                    "effect": "allow_with_obligations",
                    "conditions": {"action.mode": "read"},
                    "obligations": [
                        {"obligation_id": "receipt", "kind": "receipt"}
                    ],
                    "constraints": [
                        {
                            "constraint_id": "bounded-output",
                            "kind": "output_limit",
                            "value": 4096,
                        }
                    ],
                }
            ],
        }
    )
    runtime = build_connector_runtime(
        connectors={
            "host_probe": {
                "enabled": True,
                "backend": "local_shell_readonly",
                "allowlist": [{"action": "uptime", "command": "uptime"}],
            }
        },
        profile_root=None,
        mutating_allowed=False,
        policy_pack=pack,
        operation_id="op-policy-controls",
        target_criticality="low",
    )

    with patch("rexecop.connectors.local_shell.subprocess.run") as run_mock:
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_probe",
                action="uptime",
                target="host",
                mode="dry_run",
            )
        )

    run_mock.assert_not_called()
    assert response.success is False
    assert response.data["policy_reason_code"] == "unsupported_policy_controls"
    assert response.data["policy_blockers"] == [
        "unsupported_obligation:receipt:receipt",
        "unsupported_constraint:bounded-output:output_limit",
    ]


def test_plan_persists_policy_pack_and_verdict(tmp_path: Path) -> None:
    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=POLICY_ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )

    assert operation.metadata["policy_pack"]["policy_id"] == "rexecop-runtime-fixture"
    assert operation.metadata["target_criticality"] == "low"
    assert operation.metadata["policy_verdict"]["decision"] == "allow"
    assert operation.metadata["policy_verdict"]["reason_code"] == "fixture_read_allowed"
    plan = controller.store.load_plan(operation.id)
    assert plan.govengine_request_preview["policy_decision"]["decision"] == "allow"


def test_plan_projects_supported_operation_policy_obligations(tmp_path: Path) -> None:
    env_path = tmp_path / "env.yaml"
    env_data = yaml.safe_load(ENVIRONMENT.read_text())
    env_data["environment"]["policy_pack"] = {
        "policy_id": "obligated-operation",
        "version": "1",
        "rules": [
            {
                "rule_id": "allow-read-operation-with-controls",
                "effect": "allow_with_obligations",
                "conditions": {
                    "action.category": "operation",
                    "action.mode": "read",
                    "action.intent": "inspect_fixture_state",
                },
                "obligations": [{"obligation_id": "receipt", "kind": "receipt"}],
                "constraints": [
                    {
                        "constraint_id": "bounded-output",
                        "kind": "output_limit",
                        "value": 4096,
                    }
                ],
            }
        ],
    }
    env_path.write_text(yaml.safe_dump(env_data))
    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))

    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=env_path,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )

    enforcement = operation.metadata["policy_enforcement"]
    assert enforcement["admission_digest"].startswith("sha256:")
    assert enforcement["admission"]["outcome"] == "allowed"
    assert enforcement["plan"]["status"] == "ready"
    assert enforcement["plan"]["controls"]["max_output_bytes"] == 4096


def test_plan_blocks_unknown_operation_policy_control(tmp_path: Path) -> None:
    env_path = tmp_path / "env.yaml"
    env_data = yaml.safe_load(ENVIRONMENT.read_text())
    env_data["environment"]["policy_pack"] = {
        "policy_id": "unknown-control",
        "version": "1",
        "rules": [
            {
                "rule_id": "unsupported",
                "effect": "allow_with_obligations",
                "conditions": {
                    "action.category": "operation",
                    "action.mode": "read",
                },
                "constraints": [
                    {
                        "constraint_id": "vendor",
                        "kind": "vendor_specific",
                        "value": True,
                    }
                ],
            }
        ],
    }
    env_path.write_text(yaml.safe_dump(env_data))
    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))

    with pytest.raises(
        RExecOpValidationError,
        match="unsupported_policy_constraint:vendor_specific",
    ):
        controller.plan(
            profile_path=PROFILE,
            environment_path=env_path,
            intent="inspect_fixture_state",
            target="fixture-target",
            mode="dry_run",
        )


def test_plan_fails_closed_without_operation_allow_rule(tmp_path: Path) -> None:
    env_path = tmp_path / "env.yaml"
    env_data = yaml.safe_load(ENVIRONMENT.read_text())
    env_data["environment"]["policy_pack"] = {
        "policy_id": "connector-only",
        "version": "1",
        "rules": [
            {
                "rule_id": "allow-read-connectors-only",
                "effect": "allow",
                "conditions": {
                    "action.category": "connector",
                    "action.mode": "read",
                },
            }
        ],
    }
    env_path.write_text(yaml.safe_dump(env_data))
    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))

    with pytest.raises(RExecOpValidationError, match="operation policy denied"):
        controller.plan(
            profile_path=PROFILE,
            environment_path=env_path,
            intent="inspect_fixture_state",
            target="fixture-target",
            mode="dry_run",
        )


def test_plan_rejects_invalid_policy_pack(tmp_path: Path) -> None:
    env_path = tmp_path / "env.yaml"
    env_data = yaml.safe_load(ENVIRONMENT.read_text())
    env_data["environment"]["policy_pack"] = {"policy_id": "empty", "version": "1"}
    env_path.write_text(yaml.safe_dump(env_data))
    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    with pytest.raises(RExecOpValidationError, match="invalid policy_pack"):
        controller.plan(
            profile_path=PROFILE,
            environment_path=env_path,
            intent="inspect_fixture_state",
            target="fixture-target",
            mode="dry_run",
        )


def test_environment_loader_reads_policy_pack() -> None:
    env = load_environment(ENVIRONMENT)
    assert env.policy_pack is None
    loaded = load_environment(POLICY_ENVIRONMENT)
    assert loaded.policy_pack is not None
    assert loaded.policy_pack["policy_id"] == "rexecop-runtime-fixture"
