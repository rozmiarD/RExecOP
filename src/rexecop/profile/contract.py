from __future__ import annotations

from typing import Any

from rexecop.errors import RExecOpValidationError

REQUIRED_PROFILE_SECTIONS = (
    "intents",
    "workflows",
    "connector_requirements",
    "risk_classes",
    "evidence_requirements",
    "governance_expectations",
    "validation_rules",
    "escalation_rules",
)


def validate_profile_contract(data: dict[str, Any]) -> dict[str, Any]:
    contract = data.get("profile_contract")
    if not isinstance(contract, dict):
        raise RExecOpValidationError("missing profile_contract")

    name = str(contract.get("name") or "").strip()
    version = str(contract.get("version") or "").strip()
    if not name:
        raise RExecOpValidationError("profile_contract.name is required")
    if not version:
        raise RExecOpValidationError("profile_contract.version is required")

    for section in REQUIRED_PROFILE_SECTIONS:
        section_value = contract.get(section)
        if not isinstance(section_value, dict):
            raise RExecOpValidationError(f"profile_contract.{section} is required")
        if not section_value.get("required", False):
            raise RExecOpValidationError(f"profile_contract.{section}.required must be true")

    rollback = contract.get("rollback_rules")
    if rollback is not None and not isinstance(rollback, dict):
        raise RExecOpValidationError("profile_contract.rollback_rules must be a mapping")

    return contract
