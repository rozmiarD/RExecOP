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


class _InvalidYamlStructure(RExecOpValidationError):
    """Private stable failure for bounded YAML input parsing."""

    reason_code = "invalid_yaml_structure"
    public_message = "YAML input is invalid or exceeds structural limits"

    def __init__(self) -> None:
        super().__init__(self.public_message)


class _InvalidJsonValue(RExecOpValidationError):
    """Private stable failure for values that cannot be encoded as finite JSON."""

    reason_code = "invalid_json_value"
    public_message = "value is not finite JSON-compatible data"

    def __init__(self) -> None:
        super().__init__(self.public_message)


class RExecOpGovernanceDecisionError(RExecOpValidationError):
    """Stable runtime-consumer failure for one GovEngine decision."""

    public_message = "runtime rejected the governance decision"

    def __init__(
        self,
        reason_code: str,
        *,
        context: tuple[str, ...] = (),
    ) -> None:
        self.reason_code = reason_code
        self.context = context
        detail = f": {','.join(context)}" if context else ""
        super().__init__(f"{reason_code}{detail}")


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


class RExecOpMutationNotCertified(RExecOpValidationError):
    """Mutating execution is disabled by the runtime release posture."""

    reason_code = "mutation_not_certified"
    public_message = "mutating execution is not certified by this runtime posture"
