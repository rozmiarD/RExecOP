from __future__ import annotations

from pathlib import Path

import yaml
from helpers.staging_http_server import StagingHttpServer

from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"


def _staging_environment(server: StagingHttpServer) -> dict:
    return {
        "environment": {
            "id": "staging-http",
            "profile": "tecrax",
            "targets": {
                "all_critical_vms": {
                    "type": "group",
                    "members": ["vm-zabbix-01"],
                }
            },
            "connectors": {
                "proxmox": {
                    "enabled": True,
                    "backend": "http_api",
                    "base_url": server.base_url,
                    "actions": {
                        "list_vms": {
                            "method": "GET",
                            "path": "/proxmox/vms",
                            "unwrap": "vms",
                        }
                    },
                },
                "pbs": {
                    "enabled": True,
                    "backend": "http_api",
                    "base_url": server.base_url,
                    "actions": {
                        "list_snapshots": {
                            "method": "GET",
                            "path": "/pbs/snapshots",
                            "unwrap": "snapshots",
                        }
                    },
                },
            },
            "safety": {
                "default_mode": "dry_run",
                "apply_requires_govengine": True,
                "secrets_source": "external",
            },
        }
    }


def test_readonly_check_backup_status_against_staging_http(tmp_path: Path) -> None:
    server = StagingHttpServer()
    server.start()
    env_path = tmp_path / "staging.yaml"
    env_path.write_text(yaml.safe_dump(_staging_environment(server)))
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    try:
        operation = controller.plan(
            profile_path=PROFILE,
            environment_path=env_path,
            intent="check_backup_status",
            target="all_critical_vms",
            mode="dry_run",
        )
        completed = controller.start(operation.id)
        assert completed.state == OperationState.COMPLETED.value
        validation = controller.validate(operation.id)
        assert validation["passed"] is True
        events = store.list_evidence_events(operation.id)
        payloads = [event.get("payload") or {} for event in events]
        serialized = yaml.safe_dump(payloads)
        assert "secret-token" not in serialized
        assert "api_key" not in serialized.lower() or "[REDACTED]" in serialized
    finally:
        server.stop()


def test_apply_restart_and_rollback_drill_on_staging_http(tmp_path: Path) -> None:
    server = StagingHttpServer()
    server.start()
    env_path = tmp_path / "staging-apply.yaml"
    env_data = _staging_environment(server)
    env_data["environment"]["connectors"]["proxmox"]["actions"]["restart"] = {
        "method": "POST",
        "path": "/proxmox/restart",
        "mutating": True,
        "body": {"target": "{target}"},
    }
    env_path.write_text(yaml.safe_dump(env_data))

    adapter = StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED)
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store, govengine_adapter=adapter)
    try:
        operation = controller.plan(
            profile_path=PROFILE,
            environment_path=env_path,
            intent="restart_zabbix_agent",
            target="vm-zabbix-01",
            mode="apply",
        )
        assert operation.state == OperationState.APPROVED.value
        completed = controller.start(operation.id)
        assert completed.state == OperationState.COMPLETED.value
        assert server.restart_calls == 1

        failed = controller.get_operation(operation.id)
        failed.state = OperationState.FAILED.value
        store.save_operation(failed)
        rollback = controller.rollback(operation.id)
        assert rollback["success"] is True
        assert "rollback_marker" in rollback["executed_steps"]
    finally:
        server.stop()
