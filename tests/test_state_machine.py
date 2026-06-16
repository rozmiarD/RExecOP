from __future__ import annotations

import pytest

from rexecop.errors import RExecOpStateError
from rexecop.operation.state import OperationState, validate_transition


def test_allowed_transition_created_to_planned() -> None:
    result = validate_transition(OperationState.CREATED, OperationState.PLANNED)
    assert result == OperationState.PLANNED


def test_invalid_transition_raises() -> None:
    with pytest.raises(RExecOpStateError):
        validate_transition(OperationState.CREATED, OperationState.RUNNING)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OperationState.PLANNED, OperationState.APPROVED),
        (OperationState.APPROVED, OperationState.RUNNING),
        (OperationState.RUNNING, OperationState.VALIDATING),
        (OperationState.VALIDATING, OperationState.COMPLETED),
        (OperationState.FAILED, OperationState.ESCALATED),
    ],
)
def test_common_allowed_transitions(current: OperationState, target: OperationState) -> None:
    assert validate_transition(current, target) == target
