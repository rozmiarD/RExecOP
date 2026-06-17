from __future__ import annotations

TIMEOUT = "timeout"
TRANSIENT = "transient_connector_error"
POLICY_DENIED = "policy_denied"
UNSUPPORTED = "unsupported"
AUTH_FAILED = "auth_failed"
VALIDATION_FAILED = "validation_failed"
CONNECTOR_DISABLED = "connector_disabled"
CAPABILITY_UNDECLARED = "capability_undeclared"

TRANSIENT_CLASSES = frozenset({TIMEOUT, TRANSIENT})

READ_ONLY_MODES = frozenset({"dry_run", "observe", "emergency_readonly"})
