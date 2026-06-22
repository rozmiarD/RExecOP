from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.composite_runtime import build_connector_runtime
from rexecop.connectors.ssh_readonly import SshReadonlyRuntime
from rexecop.environment.loader import load_environment
from rexecop.environment.model import Environment
from rexecop.environment.targets import describe_target, validate_operation_target
from rexecop.errors import RExecOpValidationError
from rexecop.execution.executor import StepExecutor
from rexecop.operation.controller import OperationController
from rexecop.profile.loader import LoadedProfile, load_profile
from rexecop.storage.file_store import FileStore
from rexecop.validation.validator import validate_operation_result
from rexecop.workflow.contract import validate_workflow_contract
from rexecop.workflow.loader import load_workflow
from rexecop.workflow.model import Workflow, WorkflowStep
from rexecop.workflow.runner import WorkflowRunner

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"
WORKFLOW = PROFILE.parent / "workflows/check_backup_status.yaml"


def test_target_group_semantics_for_all_critical_vms() -> None:
    environment = load_environment(ENVIRONMENT)
    info = describe_target(environment, "all_critical_vms")
    assert info["kind"] == "group"
    assert info["members"] == ["vm-zabbix-01", "vm-pbs-01"]


def test_target_member_semantics() -> None:
    environment = load_environment(ENVIRONMENT)
    info = describe_target(environment, "vm-zabbix-01")
    assert info["kind"] == "member"
    assert info["group"] == "all_critical_vms"
    validate_operation_target(environment, "vm-zabbix-01")


def test_plan_rejects_unknown_target(tmp_path: Path) -> None:
    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    with pytest.raises(RExecOpValidationError, match="unknown environment target"):
        controller.plan(
            profile_path=PROFILE,
            environment_path=ENVIRONMENT,
            intent="check_backup_status",
            target="missing-target",
            mode="dry_run",
        )


def test_plan_rejects_missing_connector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / "env.yaml"
    env_data = yaml.safe_load(ENVIRONMENT.read_text())
    env_data["environment"]["connectors"].pop("pbs")
    env_path.write_text(yaml.safe_dump(env_data))
    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    with pytest.raises(RExecOpValidationError, match="connector not configured"):
        controller.plan(
            profile_path=PROFILE,
            environment_path=env_path,
            intent="check_backup_status",
            target="all_critical_vms",
            mode="dry_run",
        )


def test_plan_rejects_disabled_connector(tmp_path: Path) -> None:
    env_path = tmp_path / "env.yaml"
    env_data = yaml.safe_load(ENVIRONMENT.read_text())
    env_data["environment"]["connectors"]["pbs"]["enabled"] = False
    env_path.write_text(yaml.safe_dump(env_data))
    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    with pytest.raises(RExecOpValidationError, match="connector disabled: pbs"):
        controller.plan(
            profile_path=PROFILE,
            environment_path=env_path,
            intent="check_backup_status",
            target="all_critical_vms",
            mode="dry_run",
        )


def test_workflow_contract_rejects_changed_command_argv(tmp_path: Path) -> None:
    connectors_dir = tmp_path / "connectors"
    connectors_dir.mkdir()
    (connectors_dir / "host_probe.yaml").write_text(
        yaml.safe_dump(
            {
                "connector": {
                    "name": "host_probe",
                    "backend": "ssh_readonly",
                    "capabilities": ["read_root_filesystem"],
                    "command_shapes": {
                        "read_root_filesystem": {
                            "command": "df",
                            "args": ["-P", "/"],
                        }
                    },
                }
            }
        )
    )
    profile = LoadedProfile(root=tmp_path, contract={}, name="fixture", version="1")
    environment = Environment(
        id="fixture",
        profile="fixture",
        description="",
        targets={"host": {"type": "host"}},
        connectors={
            "host_probe": {
                "enabled": True,
                "backend": "ssh_readonly",
                "allowlist": [
                    {
                        "action": "read_root_filesystem",
                        "command": "df",
                        "args": ["-P"],
                    }
                ],
            }
        },
    )
    workflow = Workflow(
        id="fixture.inventory",
        intent="inventory",
        mode="read_only",
        risk="low",
        description="",
        steps=[
            WorkflowStep.from_dict(
                {
                    "id": "root_filesystem",
                    "type": "connector",
                    "connector": "host_probe",
                    "action": "read_root_filesystem",
                }
            )
        ],
    )
    with pytest.raises(RExecOpValidationError, match="command shape mismatch"):
        validate_workflow_contract(workflow, environment, profile)


def test_workflow_contract_rejects_escape_step_type() -> None:
    environment = load_environment(ENVIRONMENT)
    workflow = load_workflow(WORKFLOW)
    escaped = Workflow(
        id=workflow.id,
        intent=workflow.intent,
        mode=workflow.mode,
        risk=workflow.risk,
        description=workflow.description,
        steps=workflow.steps + [WorkflowStep.from_dict({
            "id": "escape",
            "type": "shell",
            "action": "rm -rf /",
        })],
        retry=workflow.retry,
        rollback=workflow.rollback,
    )
    with pytest.raises(RExecOpValidationError, match="unsupported workflow step type"):
        validate_workflow_contract(escaped, environment)


def test_workflow_runner_does_not_execute_undeclared_steps() -> None:
    planned = [
        {"id": "only", "type": "internal", "action": "record_rollback_marker"},
    ]
    runner = WorkflowRunner(StepExecutor(internal_handlers={}))
    result = runner.run(
        operation_id="op-1",
        target="all_critical_vms",
        mode="dry_run",
        planned_steps=planned,
        correlation_id="corr-1",
        shared_state={"extra_steps": [{"id": "evil", "type": "internal", "action": "x"}]},
    )
    assert result.executed_steps == ["only"]


def test_composite_rejects_disabled_connector_at_invoke() -> None:
    runtime = build_connector_runtime(
        connectors={"proxmox": {"enabled": False, "backend": "mock"}},
        profile_root=str(PROFILE.parent),
        mutating_allowed=False,
    )
    response = runtime.invoke(
        ConnectorRequest(connector="proxmox", action="list_vms", target="t", mode="dry_run")
    )
    assert not response.success


def test_validator_rejects_missing_validation_rules() -> None:
    profile = load_profile(PROFILE)
    with pytest.raises(RExecOpValidationError, match="no validation rules"):
        validate_operation_result(
            intent="unknown_intent",
            shared_state={},
            profile=profile,
        )


def test_ssh_readonly_strict_known_hosts_and_quoting() -> None:
    runtime = SshReadonlyRuntime(
        connector_name="host_ro",
        config={
            "host": "pve-01.example.com",
            "user": "readonly",
            "known_hosts_policy": "strict",
            "known_hosts_file": "/etc/ssh/ssh_known_hosts",
            "allowlist": [{"action": "uptime", "command": "uptime"}],
        },
    )
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        class Result:
            returncode = 0
            stdout = "up"
            stderr = ""

        return Result()

    with patch("rexecop.connectors.ssh_readonly.subprocess.run", side_effect=fake_run):
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_ro",
                action="uptime",
                target="local",
                mode="dry_run",
            )
        )
    assert response.success
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "StrictHostKeyChecking=yes" in argv
    assert "UserKnownHostsFile=/etc/ssh/ssh_known_hosts" in argv
    assert argv[-1] == "uptime"


def test_file_store_writes_valid_json_without_tmp_artifacts(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    from rexecop.operation.model import Operation

    operation = Operation(
        id="op-atomic",
        profile="p",
        environment="e",
        intent="i",
        target="all_critical_vms",
        mode="dry_run",
        state="planned",
        requested_by="test",
        created_at="2026-06-20T00:00:00+00:00",
        updated_at="2026-06-20T00:00:00+00:00",
    )
    store.save_operation(operation)
    assert store.load_operation("op-atomic").id == "op-atomic"
    assert list(store.operations_dir.glob("*.tmp")) == []
