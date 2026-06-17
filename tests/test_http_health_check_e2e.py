from __future__ import annotations

from pathlib import Path

import yaml

from helpers.health_staging_http_server import HealthStagingHttpServer
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/http-health-fixture/profile.yaml"


def _health_environment(server: HealthStagingHttpServer) -> dict:
    return {
        "environment": {
            "id": "http-health-staging",
            "profile": "http-health-fixture",
            "targets": {
                "api_primary": {
                    "type": "endpoint",
                    "name": "primary",
                }
            },
            "connectors": {
                "health": {
                    "enabled": True,
                    "backend": "http_api",
                    "base_url": server.base_url,
                    "actions": {
                        "ping": {
                            "method": "GET",
                            "path": "/health",
                        }
                    },
                }
            },
            "safety": {
                "default_mode": "dry_run",
                "apply_requires_govengine": True,
                "secrets_source": "external",
            },
        }
    }


def test_http_health_check_e2e_without_domain_internals(tmp_path: Path) -> None:
    server = HealthStagingHttpServer()
    server.start()
    env_path = tmp_path / "health.yaml"
    env_path.write_text(yaml.safe_dump(_health_environment(server)))
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    try:
        operation = controller.plan(
            profile_path=PROFILE,
            environment_path=env_path,
            intent="http_health_check",
            target="api_primary",
            mode="dry_run",
        )
        completed = controller.start(operation.id)
        assert completed.state == OperationState.COMPLETED.value
        validation = controller.validate(operation.id)
        assert validation["passed"] is True
        assert validation["rule"] == "http_health_check.probe_ok"
    finally:
        server.stop()
