from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from helpers.staging_http_server import StagingHttpServer
from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest, ConnectorResponse
from rexecop.connectors.composite_runtime import build_connector_runtime
from rexecop.connectors.static_fixture import StaticFixtureRuntime
from rexecop.errors import RExecOpValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = REPO_ROOT / "examples/profiles/runtime-fixture"


class _PluginRuntime:
    def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
        return ConnectorResponse(
            connector=request.connector,
            action=request.action,
            success=True,
            data={"status": "plugin-ready"},
        )


class _PluginEntryPoint:
    name = "fixture_plugin"

    def __init__(self, loaded: object) -> None:
        self.loaded = loaded
        self.load_calls = 0

    def load(self) -> object:
        self.load_calls += 1
        return self.loaded


def _plugin_factory(**_kwargs: object) -> _PluginRuntime:
    return _PluginRuntime()

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
                    "deployment_posture": "fixture",
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
        assert response.data["observed"] is True
        assert response.data["status"] == "ready"
        assert response.data["observed_destination_binding"]["address_class"] == "loopback"
    finally:
        server.stop()


def test_registered_plugin_factory_is_lazy_until_invoke() -> None:
    point = _PluginEntryPoint(_plugin_factory)
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        return_value=[point],
    ):
        runtime = build_connector_runtime(
            connectors={
                "plugin_source": {
                    "enabled": True,
                    "backend": "fixture_plugin",
                }
            },
            profile_root=None,
            mutating_allowed=False,
        )
        assert point.load_calls == 0
        assert "plugin_source" not in runtime._backends

        response = runtime.invoke(
            ConnectorRequest(
                connector="plugin_source",
                action="inspect",
                target="fixture-target",
                mode="dry_run",
            )
        )

    assert response.success is True
    assert point.load_calls == 1
    assert response.data == {"status": "plugin-ready"}


def test_registered_plugin_load_failure_has_no_mock_fallback() -> None:
    point = _PluginEntryPoint(object())
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        return_value=[point],
    ):
        runtime = build_connector_runtime(
            connectors={
                "plugin_source": {
                    "enabled": True,
                    "backend": "fixture_plugin",
                }
            },
            profile_root=None,
            mutating_allowed=False,
        )
        with pytest.raises(RExecOpValidationError, match="factory unavailable"):
            runtime.invoke(
                ConnectorRequest(
                    connector="plugin_source",
                    action="inspect",
                    target="fixture-target",
                    mode="dry_run",
                )
            )

    assert point.load_calls == 1


def test_arbitrary_unregistered_backend_is_denied_without_mock_fallback() -> None:
    with pytest.raises(RExecOpValidationError, match="backend unavailable"):
        build_connector_runtime(
            connectors={
                "plugin_source": {
                    "enabled": True,
                    "backend": "arbitrary_plugin",
                }
            },
            profile_root=None,
            mutating_allowed=False,
        )
