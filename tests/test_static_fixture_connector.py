from __future__ import annotations

from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.static_fixture import StaticFixtureRuntime


def _runtime(*, mutating_allowed: bool = False) -> StaticFixtureRuntime:
    return StaticFixtureRuntime(
        connector_name="fixture_source",
        mutating_allowed=mutating_allowed,
        config={
            "fixture_only": True,
            "actions": {
                "read_fixture_state": {
                    "data": {"observed": True, "state": "ready"},
                },
                "apply_fixture_change": {
                    "mutating": True,
                    "data": {
                        "before_state": {"changed": False},
                        "after_state": {"changed": True},
                    },
                },
            },
        },
    )


def test_static_fixture_returns_configured_bounded_data() -> None:
    result = _runtime().invoke(
        ConnectorRequest(
            connector="fixture_source",
            action="read_fixture_state",
            target="fixture-target",
            mode="dry_run",
        )
    )
    assert result.success is True
    assert result.data == {"observed": True, "state": "ready"}


def test_static_fixture_mutation_requires_apply_and_admission() -> None:
    request = ConnectorRequest(
        connector="fixture_source",
        action="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    assert _runtime().invoke(request).success is False
    admitted = _runtime(mutating_allowed=True).invoke(request)
    assert admitted.success is True
    assert admitted.data["after_state"] == {"changed": True}


def test_static_fixture_fails_closed_without_fixture_marker() -> None:
    runtime = StaticFixtureRuntime(
        connector_name="fixture_source",
        config={"actions": {}},
        mutating_allowed=False,
    )
    result = runtime.invoke(
        ConnectorRequest(
            connector="fixture_source",
            action="read_fixture_state",
            target="fixture-target",
            mode="dry_run",
        )
    )
    assert result.success is False
    assert "fixture_only" in result.error
