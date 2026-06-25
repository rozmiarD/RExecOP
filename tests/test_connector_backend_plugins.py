from __future__ import annotations

from unittest.mock import patch

from rexecop.connectors.base import ConnectorRequest, ConnectorResponse
from rexecop.connectors.composite_runtime import build_connector_runtime
from rexecop.connectors.fixture_loader import (
    list_registered_connector_backends,
    load_connector_backend_for_connector,
)


class FixturePluginRuntime:
    def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
        return ConnectorResponse(
            connector=request.connector,
            action=request.action,
            success=True,
            data={"status": "ready"},
        )


def build_fixture_plugin(**_: object) -> FixturePluginRuntime:
    return FixturePluginRuntime()


class FixtureEntryPoint:
    name = "fixture_plugin"

    def load(self):
        return build_fixture_plugin


def _entry_points(**_: object) -> list[FixtureEntryPoint]:
    return [FixtureEntryPoint()]


@patch("rexecop.connectors.fixture_loader.entry_points", side_effect=_entry_points)
def test_connector_backend_entry_point_is_discovered(_entry_points_mock) -> None:
    assert list_registered_connector_backends() == ["fixture_plugin"]


@patch("rexecop.connectors.fixture_loader.entry_points", side_effect=_entry_points)
def test_connector_backend_factory_is_loaded(_entry_points_mock) -> None:
    runtime = load_connector_backend_for_connector(
        "fixture_plugin",
        connector_name="fixture_source",
        config={},
        profile_root=None,
        mutating_allowed=False,
    )
    assert runtime is not None
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


@patch("rexecop.connectors.fixture_loader.entry_points", side_effect=_entry_points)
def test_composite_routes_registered_plugin(_entry_points_mock) -> None:
    runtime = build_connector_runtime(
        connectors={
            "fixture_source": {
                "enabled": True,
                "backend": "fixture_plugin",
            }
        },
        profile_root=None,
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
    assert response.data == {"status": "ready"}
