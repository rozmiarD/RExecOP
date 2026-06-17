from __future__ import annotations

import pytest

from rexecop.connectors.fixture_loader import list_registered_connector_backends
from rexecop.execution.internal_registry import list_registered_internal_actions

tecrax = pytest.importorskip("tecrax")


def test_tecrax_internal_actions_registered() -> None:
    actions = list_registered_internal_actions()
    assert "environment.resolve_targets" in actions
    assert "correlate_vm_backup_coverage" in actions
    assert "record_rollback_marker" in actions


def test_tecrax_fixture_backend_registered() -> None:
    backends = list_registered_connector_backends()
    assert "tecrax_fixture" in backends
