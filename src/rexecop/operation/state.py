from __future__ import annotations

from enum import StrEnum

from rexecop.errors import RExecOpStateError


class OperationState(StrEnum):
    CREATED = "created"
    PLANNED = "planned"
    BLOCKED = "blocked"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    APPROVED = "approved"
    RUNNING = "running"
    PAUSED = "paused"
    RESUMING = "resuming"
    RETRYING = "retrying"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ESCALATED = "escalated"


ALLOWED_TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    OperationState.CREATED: frozenset({OperationState.PLANNED}),
    OperationState.PLANNED: frozenset(
        {
            OperationState.BLOCKED,
            OperationState.WAITING_FOR_APPROVAL,
            OperationState.APPROVED,
        }
    ),
    OperationState.APPROVED: frozenset({OperationState.RUNNING}),
    OperationState.RUNNING: frozenset(
        {
            OperationState.PAUSED,
            OperationState.RETRYING,
            OperationState.VALIDATING,
            OperationState.FAILED,
            OperationState.CANCELLED,
        }
    ),
    OperationState.PAUSED: frozenset({OperationState.RESUMING}),
    OperationState.RESUMING: frozenset({OperationState.RUNNING}),
    OperationState.RETRYING: frozenset({OperationState.RUNNING}),
    OperationState.VALIDATING: frozenset({OperationState.COMPLETED, OperationState.FAILED}),
    OperationState.FAILED: frozenset({OperationState.ESCALATED}),
    OperationState.BLOCKED: frozenset({OperationState.ESCALATED}),
    OperationState.WAITING_FOR_APPROVAL: frozenset(
        {OperationState.APPROVED, OperationState.CANCELLED}
    ),
    OperationState.COMPLETED: frozenset(),
    OperationState.CANCELLED: frozenset(),
    OperationState.ESCALATED: frozenset(),
}


def validate_transition(
    current: OperationState | str,
    target: OperationState | str,
) -> OperationState:
    from_state = (
        current if isinstance(current, OperationState) else OperationState(str(current))
    )
    to_state = target if isinstance(target, OperationState) else OperationState(str(target))
    allowed = ALLOWED_TRANSITIONS.get(from_state, frozenset())
    if to_state not in allowed:
        raise RExecOpStateError(
            f"invalid transition: {from_state.value} -> {to_state.value}"
        )
    return to_state
