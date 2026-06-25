from __future__ import annotations

from rexecop.execution.internal_registry import list_registered_internal_actions


def test_builtin_internal_actions_registered() -> None:
    actions = list_registered_internal_actions()
    assert "record_execution_checkpoint" in actions
    assert "record_rollback_marker" in actions
