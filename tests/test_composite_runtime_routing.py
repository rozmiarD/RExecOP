from __future__ import annotations

from pathlib import Path

import pytest

from helpers.staging_http_server import StagingHttpServer
from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.composite_runtime import build_connector_runtime

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = REPO_ROOT / "examples/profiles/tecrax-fixture"

tecrax_fixture = pytest.importorskip("tecrax.fixture.mock_runtime")


def test_composite_defaults_to_mock_backend() -> None:
    runtime = build_connector_runtime(
        connectors={"proxmox": {"enabled": True}},
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
    assert not response.success
    assert "unsupported mock" in (response.error or "")


def test_composite_disabled_connector_is_rejected() -> None:
    runtime = build_connector_runtime(
        connectors={"proxmox": {"enabled": False, "backend": "http_api"}},
        profile_root=str(PROFILE_ROOT),
        mutating_allowed=False,
    )
    response = runtime.invoke(
        ConnectorRequest(connector="proxmox", action="list_vms", target="t", mode="dry_run")
    )
    assert not response.success
    assert response.data["error_class"] == connector_errors.CONNECTOR_DISABLED


def test_composite_unknown_connector_is_rejected() -> None:
    runtime = build_connector_runtime(
        connectors={"proxmox": {"enabled": True}},
        profile_root=str(PROFILE_ROOT),
        mutating_allowed=False,
    )
    response = runtime.invoke(
        ConnectorRequest(connector="pbs", action="list_snapshots", target="t", mode="dry_run")
    )
    assert not response.success
    assert "not configured" in (response.error or "")


def test_composite_routes_fixture_entry_point_backend() -> None:
    runtime = build_connector_runtime(
        connectors={
            "proxmox": {
                "enabled": True,
                "backend": "tecrax_fixture",
            }
        },
        profile_root=str(PROFILE_ROOT),
        mutating_allowed=False,
    )
    backend = runtime._backends["proxmox"]
    assert isinstance(backend, tecrax_fixture.TecraxFixtureConnectorRuntime)
    response = runtime.invoke(
        ConnectorRequest(
            connector="proxmox",
            action="list_vms",
            target="all_critical_vms",
            mode="dry_run",
        )
    )
    assert response.success
    assert response.data["vms"]


def test_composite_routes_legacy_fixture_field() -> None:
    runtime = build_connector_runtime(
        connectors={
            "proxmox": {
                "enabled": True,
                "fixture": "tecrax_fixture",
            }
        },
        profile_root=str(PROFILE_ROOT),
        mutating_allowed=False,
    )
    backend = runtime._backends["proxmox"]
    assert isinstance(backend, tecrax_fixture.TecraxFixtureConnectorRuntime)
    response = runtime.invoke(
        ConnectorRequest(
            connector="proxmox",
            action="list_vms",
            target="all_critical_vms",
            mode="dry_run",
        )
    )
    assert response.success


def test_composite_routes_http_api_backend() -> None:
    server = StagingHttpServer()
    server.start()
    try:
        runtime = build_connector_runtime(
            connectors={
                "proxmox": {
                    "enabled": True,
                    "backend": "http_api",
                    "base_url": server.base_url,
                    "actions": {
                        "list_vms": {"method": "GET", "path": "/proxmox/vms", "unwrap": "vms"},
                    },
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
