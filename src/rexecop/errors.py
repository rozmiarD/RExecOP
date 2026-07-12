class RExecOpError(Exception):
    """Base error for RExecOp runtime failures."""

    reason_code = "runtime_error"
    public_message = "runtime operation failed"


class RExecOpStateError(RExecOpError):
    """Invalid or disallowed operation state transition."""

    reason_code = "invalid_state_transition"
    public_message = "operation state does not allow this action"


class RExecOpValidationError(RExecOpError):
    """Contract or input validation failure."""

    reason_code = "validation_error"
    public_message = "runtime input or contract validation failed"


class RExecOpConcurrencyConflict(RExecOpError):
    """A compare-and-swap write lost a race with another runtime process."""

    code = "concurrency_conflict"
    reason_code = "concurrency_conflict"
    public_message = "operation changed concurrently; reload and retry safely"


class RExecOpUnsafeDestination(RExecOpValidationError):
    """Destination binding or egress posture is unsafe."""

    reason_code = "unsafe_destination"
    public_message = "connector destination is not allowed by the declared posture"


class RExecOpLeaseLost(RExecOpValidationError):
    """The process no longer owns a fresh execution lease."""

    reason_code = "lease_lost"
    public_message = "execution lease was lost; recover runtime ownership before retry"


class RExecOpOutcomeIndeterminate(RExecOpValidationError):
    """Backend IO may have happened but no durable result exists."""

    reason_code = "outcome_indeterminate"
    public_message = "connector outcome is indeterminate and requires reconciliation"
