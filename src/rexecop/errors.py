class RExecOpError(Exception):
    """Base error for RExecOp runtime failures."""


class RExecOpStateError(RExecOpError):
    """Invalid or disallowed operation state transition."""


class RExecOpValidationError(RExecOpError):
    """Contract or input validation failure."""
