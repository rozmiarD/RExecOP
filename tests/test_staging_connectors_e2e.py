from __future__ import annotations

import json
from pathlib import Path

import yaml

from helpers.staging_http_server import StagingHttpServer
from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"


def _staging_environment(server: StagingHttpServer) -> dict:
    return {
        "environment": {
            "id": "staging-http",
            "profile": "runtime_fixture",
            "targets": {
                "fixture-target": {
                    "type": "fixture",
                }
            },
            "connectors": {
                "fixture_source": {
                    "enabled": True,
                    "backend": "http_api",
                    "base_url": server.base_url,
                    "actions": {
                        "read_fixture_state": {
                            "method": "GET",
                            "path": "/fixture/state",
                            "unwrap": "state",
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


def test_readonly_inspect_fixture_state_against_staging_http(tmp_path: Path) -> None:
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
            intent="inspect_fixture_state",
            target="fixture-target",
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

        bundle_dir = store.operation_sclite_dir(operation.id)
        receipt = json.loads(
            (bundle_dir / "05_execution_receipt.json").read_text(encoding="utf-8")
        )
        assert receipt["execution"]["executed_command_count"] == 1
        assert receipt["execution"]["network_execution_performed"] is False
    finally:
        server.stop()


def test_apply_restart_and_rollback_drill_on_staging_http(tmp_path: Path) -> None:
    server = StagingHttpServer()
    server.start()
    env_path = tmp_path / "staging-apply.yaml"
    env_data = _staging_environment(server)
    env_data["environment"]["connectors"]["fixture_source"]["actions"][
        "apply_fixture_change"
    ] = {
        "method": "POST",
        "path": "/fixture/change",
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
            intent="apply_fixture_change",
            target="fixture-target",
            mode="apply",
        )
        assert operation.state == OperationState.APPROVED.value
        completed = controller.start(operation.id)
        assert completed.state == OperationState.COMPLETED.value
        assert server.change_calls == 1

        failed = controller.get_operation(operation.id)
        failed.state = OperationState.FAILED.value
        store.save_operation(failed)
        rollback = controller.rollback(operation.id)
        assert rollback["success"] is True
        assert "rollback_marker" in rollback["executed_steps"]
    finally:
        server.stop()
