from __future__ import annotations

from pathlib import Path

from helpers.staging_http_server import StagingHttpServer
from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.composite_runtime import build_connector_runtime
from rexecop.connectors.static_fixture import StaticFixtureRuntime

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = REPO_ROOT / "examples/profiles/runtime-fixture"

def test_composite_defaults_to_mock_backend() -> None:
    runtime = build_connector_runtime(
        connectors={"source": {"enabled": True}},
        profile_root=str(PROFILE_ROOT),
        mutating_allowed=False,
    )
    response = runtime.invoke(
        ConnectorRequest(
            connector="source",
            action="read_state",
            target="fixture-target",
            mode="dry_run",
        )
    )
    assert not response.success
    assert "unsupported mock" in (response.error or "")


def test_composite_disabled_connector_is_rejected() -> None:
    runtime = build_connector_runtime(
        connectors={"source": {"enabled": False, "backend": "http_api"}},
        profile_root=str(PROFILE_ROOT),
        mutating_allowed=False,
    )
    response = runtime.invoke(
        ConnectorRequest(connector="source", action="read_state", target="t", mode="dry_run")
    )
    assert not response.success
    assert response.data["error_class"] == connector_errors.CONNECTOR_DISABLED


def test_composite_unknown_connector_is_rejected() -> None:
    runtime = build_connector_runtime(
        connectors={"source": {"enabled": True}},
        profile_root=str(PROFILE_ROOT),
        mutating_allowed=False,
    )
    response = runtime.invoke(
        ConnectorRequest(connector="missing", action="read_state", target="t", mode="dry_run")
    )
    assert not response.success
    assert "not configured" in (response.error or "")


def test_composite_routes_static_fixture_backend() -> None:
    runtime = build_connector_runtime(
        connectors={
            "fixture_source": {
                "enabled": True,
                "backend": "static_fixture",
                "fixture_only": True,
                "actions": {"read_fixture_state": {"data": {"status": "ready"}}},
            }
        },
        profile_root=str(PROFILE_ROOT),
        mutating_allowed=False,
    )
    backend = runtime._backends["fixture_source"]
    assert isinstance(backend, StaticFixtureRuntime)
    response = runtime.invoke(
        ConnectorRequest(
            connector="fixture_source",
            action="read_fixture_state",
            target="fixture-target",
            mode="dry_run",
        )
    )
    assert response.success
    assert response.data == {"status": "ready"}


def test_composite_routes_http_api_backend() -> None:
    server = StagingHttpServer()
    server.start()
    try:
        runtime = build_connector_runtime(
            connectors={
                "fixture_source": {
                    "enabled": True,
                    "backend": "http_api",
                    "base_url": server.base_url,
                    "actions": {
                        "read_fixture_state": {
                            "method": "GET",
                            "path": "/fixture/state",
                            "unwrap": "state",
                        },
                    },
                }
            },
            profile_root=str(PROFILE_ROOT),
            mutating_allowed=False,
        )
        response = runtime.invoke(
            ConnectorRequest(
                connector="fixture_source",
                action="read_fixture_state",
                target="fixture-target",
                mode="dry_run",
            )
        )
        assert response.success
        assert response.data == {"observed": True, "status": "ready"}
    finally:
        server.stop()
