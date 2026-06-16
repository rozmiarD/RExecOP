from __future__ import annotations

from typing import Any

from rexecop.errors import RExecOpValidationError


def validate_operation_result(
    *,
    intent: str,
    shared_state: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic profile hook placeholder for Phase 4."""
    if intent == "check_backup_status":
        correlation = shared_state.get("correlation")
        if not isinstance(correlation, dict):
            return {
                "passed": False,
                "rule": "check_backup_status.correlation_required",
                "details": {"reason": "missing correlation result"},
            }
        passed = bool(correlation.get("all_critical_covered"))
        return {
            "passed": passed,
            "rule": "check_backup_status.all_critical_covered",
            "details": correlation,
        }

    raise RExecOpValidationError(f"no validation rules for intent: {intent}")
