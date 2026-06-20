from __future__ import annotations

from pathlib import Path

from helpers.staging_http_server import StagingHttpServer
from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.composite_runtime import build_connector_runtime
from rexecop.connectors.fixture_loader import (
    list_registered_connector_backends,
    load_connector_backend_for_connector,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = REPO_ROOT / "examples/profiles/tecrax-fixture"

pytest = __import__("pytest")
tecrax = pytest.importorskip("tecrax")


def test_tecrax_proxmox_backend_registered() -> None:
    backends = list_registered_connector_backends()
    assert "tecrax_fixture" in backends
    assert "tecrax_proxmox" in backends


def test_tecrax_proxmox_plugin_lists_vms_from_staging_server() -> None:
    server = StagingHttpServer()
    server.start()
    try:
        runtime = build_connector_runtime(
            connectors={
                "proxmox": {
                    "enabled": True,
                    "backend": "tecrax_proxmox",
                    "staging_paths": True,
                    "base_url": server.base_url,
                }
            },
            profile_root=str(PROFILE_ROOT),
            mutating_allowed=False,
        )
        response = runtime.invoke(
            ConnectorRequest(
                connector="proxmox",
                action="list_vms",
                target="all_critical_vms",
                mode="dry_run",
            )
        )
        assert response.success
        assert len(response.data["vms"]) == 2
    finally:
        server.stop()


def test_load_connector_backend_for_connector_factory() -> None:
    server = StagingHttpServer()
    server.start()
    try:
        runtime = load_connector_backend_for_connector(
            "tecrax_proxmox",
            connector_name="proxmox",
            config={
                "staging_paths": True,
                "base_url": server.base_url,
            },
            profile_root=str(PROFILE_ROOT),
            mutating_allowed=False,
        )
        assert runtime is not None
        response = runtime.invoke(
            ConnectorRequest(
                connector="proxmox",
                action="list_vms",
                target="all_critical_vms",
                mode="dry_run",
            )
        )
        assert response.success
    finally:
        server.stop()
