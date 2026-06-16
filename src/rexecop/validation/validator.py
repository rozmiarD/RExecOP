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

    if intent == "restart_zabbix_agent":
        verification = shared_state.get("agent_after_state")
        mutation = shared_state.get("mutation_states", {}).get("restart_agent")
        if not isinstance(verification, dict):
            return {
                "passed": False,
                "rule": "restart_zabbix_agent.after_state_required",
                "details": {"reason": "missing agent_after_state"},
            }
        passed = verification.get("agent_status") == "restarted"
        details: dict[str, Any] = {
            "agent_after_state": verification,
            "mutation_states": mutation,
        }
        if isinstance(mutation, dict):
            details["before_state"] = mutation.get("before_state")
            details["after_state"] = mutation.get("after_state")
        return {
            "passed": passed,
            "rule": "restart_zabbix_agent.agent_restarted",
            "details": details,
        }

    raise RExecOpValidationError(f"no validation rules for intent: {intent}")
