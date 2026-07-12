from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from rexecop.connectors.fixture_loader import (
    list_registered_connector_backends,
    load_connector_backend_for_connector,
)
from rexecop.errors import RExecOpValidationError
from rexecop.execution.internal_registry import load_internal_handlers
from rexecop.profile.extension_manifest import build_plugin_compatibility_report
from rexecop.runtime.doctor import _check_plugin_posture


class EntryPoint:
    def __init__(self, name: str, loaded) -> None:
        self.name = name
        self._loaded = loaded

    def load(self):
        return self._loaded


def _entry_points_for(*points: EntryPoint):
    def collect(**_kwargs):
        return list(points)

    return collect


def test_connector_plugin_cannot_collide_with_builtin() -> None:
    point = EntryPoint("http_api", lambda **_kwargs: object())
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        side_effect=_entry_points_for(point),
    ):
        with pytest.raises(RExecOpValidationError, match="plugin_name_collision"):
            list_registered_connector_backends()


def test_factory_typeerror_is_not_retried_as_zero_argument() -> None:
    calls = 0

    def broken_factory(**_kwargs):
        nonlocal calls
        calls += 1
        raise TypeError("private plugin detail")

    point = EntryPoint("broken", broken_factory)
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        side_effect=_entry_points_for(point),
    ):
        with pytest.raises(TypeError):
            load_connector_backend_for_connector(
                "broken",
                connector_name="fixture",
                config={},
                profile_root=None,
                mutating_allowed=False,
            )
    assert calls == 1


def test_connector_factory_v1_signature_is_required() -> None:
    def legacy_factory(required_legacy_argument):
        return required_legacy_argument

    point = EntryPoint("legacy", legacy_factory)
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        side_effect=_entry_points_for(point),
    ):
        with pytest.raises(RExecOpValidationError, match="plugin_contract_invalid"):
            load_connector_backend_for_connector(
                "legacy",
                connector_name="fixture",
                config={},
                profile_root=None,
                mutating_allowed=False,
            )


def test_internal_action_cannot_replace_builtin() -> None:
    with pytest.raises(RExecOpValidationError, match="plugin_name_collision"):
        load_internal_handlers(extra={"record_execution_checkpoint": lambda _ctx: {}})


def test_compatibility_report_bounds_plugin_exception_text() -> None:
    secret = "private-plugin-exception-detail"

    def broken_factory(**_kwargs):
        raise RuntimeError(secret)

    point = EntryPoint("broken", broken_factory)
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        side_effect=_entry_points_for(point),
    ):
        report = build_plugin_compatibility_report()

    assert report["status"] == "failed"
    assert report["security_posture"]["execution_model"] == "trusted_in_process"
    assert secret not in json.dumps(report)


def test_stable_doctor_requires_explicit_plugin_allowlist() -> None:
    report = {
        "failed": [],
        "inventory": {
            "connector_backends": [{"name": "reviewed_connector"}],
            "internal_action_registrars": [{"name": "reviewed_actions"}],
        },
    }
    with patch("rexecop.runtime.doctor.build_plugin_compatibility_report", return_value=report):
        blocked = _check_plugin_posture("stable", None)
        passed = _check_plugin_posture("stable", "reviewed_connector,reviewed_actions")

    assert blocked["status"] == "blocker"
    assert passed["status"] == "passed"
    assert passed["details"]["execution_model"] == "trusted_in_process"
    assert passed["details"]["sandboxed"] is False
