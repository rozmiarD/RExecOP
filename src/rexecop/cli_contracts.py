from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CLI_CONTRACT_REGISTRY_SCHEMA = "rexecop.cli_contract_registry.v0.1"


@dataclass(frozen=True)
class CliExitCode:
    code: int
    meaning: str

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "meaning": self.meaning}


@dataclass(frozen=True)
class CliContract:
    command: tuple[str, ...]
    schema: str
    stability: str
    formats: tuple[str, ...] = ("json",)
    default_format: str = "json"
    exit_codes: tuple[CliExitCode, ...] = (
        CliExitCode(0, "success"),
        CliExitCode(1, "validation_error_or_blocker"),
    )
    redacted: bool = True
    bounded_output: bool = True
    authority: str = "projection"
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": " ".join(self.command),
            "argv": list(self.command),
            "schema": self.schema,
            "stability": self.stability,
            "formats": list(self.formats),
            "default_format": self.default_format,
            "exit_codes": [item.as_dict() for item in self.exit_codes],
            "redacted": self.redacted,
            "bounded_output": self.bounded_output,
            "authority": self.authority,
            "notes": list(self.notes),
        }


CLI_CONTRACTS: tuple[CliContract, ...] = (
    CliContract(
        command=("status",),
        schema="rexecop.operation_status.v0.1",
        stability="alpha_contract",
        notes=("Current command emits this shape without an explicit schema field.",),
    ),
    CliContract(
        command=("operation", "explain"),
        schema="rexecop.operation_explain.v0.1",
        stability="alpha_contract",
    ),
    CliContract(
        command=("operation", "review"),
        schema="rexecop.operation_review.v0.1",
        stability="alpha_contract",
        formats=("json", "table", "markdown"),
    ),
    CliContract(
        command=("operation", "diff"),
        schema="rexecop.operation_plan_diff.v0.1",
        stability="alpha_contract",
        formats=("json", "table", "markdown"),
        exit_codes=(
            CliExitCode(0, "unchanged"),
            CliExitCode(1, "drifted_unavailable_or_validation_error"),
        ),
    ),
    CliContract(
        command=("receipt", "show"),
        schema="rexecop.receipt_show.v0.1",
        stability="alpha_contract",
        exit_codes=(
            CliExitCode(0, "present_missing_or_partial"),
            CliExitCode(1, "broken_digest_or_validation_error"),
        ),
        authority="sclite_ref_projection",
        notes=("Does not replace SCLite artifacts or receipt truth.",),
    ),
    CliContract(
        command=("evidence", "show"),
        schema="rexecop.evidence_show.v0.1",
        stability="alpha_contract",
        authority="runtime_evidence_projection",
    ),
    CliContract(
        command=("chain", "summary"),
        schema="rexecop.chain_summary.v0.1",
        stability="alpha_contract",
        authority="digest_link_projection",
    ),
    CliContract(
        command=("support", "bundle"),
        schema="rexecop.support_bundle.v0.1",
        stability="alpha_contract",
        exit_codes=(
            CliExitCode(0, "ready_or_partial"),
            CliExitCode(1, "action_required_or_unredacted_request"),
        ),
        authority="redacted_diagnostic_projection",
        notes=("Requires --redacted.",),
    ),
    CliContract(
        command=("runtime", "status"),
        schema="rexecop.runtime_status.v0.1",
        stability="alpha_contract",
    ),
    CliContract(
        command=("ops",),
        schema="rexecop.ops.v0.1",
        stability="alpha_contract",
        exit_codes=(
            CliExitCode(0, "no_blockers"),
            CliExitCode(1, "blockers_present_or_validation_error"),
        ),
    ),
    CliContract(
        command=("dead-letter", "list"),
        schema="rexecop.dead_letter_list.v0.1",
        stability="alpha_contract",
    ),
    CliContract(
        command=("dead-letter", "show"),
        schema="rexecop.dead_letter_show.v0.1",
        stability="alpha_contract",
    ),
    CliContract(
        command=("locks", "list"),
        schema="rexecop.locks_list.v0.1",
        stability="alpha_contract",
    ),
    CliContract(
        command=("explain-error",),
        schema="rexecop.explain_error.v0.1",
        stability="alpha_contract",
    ),
    CliContract(
        command=("profile", "lint"),
        schema="rexecop.profile_conformance.v0.1",
        stability="alpha_contract",
        exit_codes=(
            CliExitCode(0, "passed"),
            CliExitCode(1, "failed_or_validation_error"),
        ),
        authority="profile_contract_projection",
    ),
)


def cli_contract_registry() -> dict[str, Any]:
    contracts = [item.as_dict() for item in sorted(CLI_CONTRACTS, key=lambda item: item.command)]
    return {
        "schema": CLI_CONTRACT_REGISTRY_SCHEMA,
        "status": "present",
        "scope": "rexecop_operator_facing_cli",
        "contract_count": len(contracts),
        "contracts": contracts,
        "non_claims": [
            "Does not execute commands.",
            "Does not validate private runtime state.",
            "Does not make table or markdown formats stable unless explicitly listed per command.",
            "Does not replace command-specific tests.",
        ],
    }


def validate_cli_contract_registry(payload: dict[str, Any] | None = None) -> list[str]:
    registry = payload or cli_contract_registry()
    errors: list[str] = []
    if registry.get("schema") != CLI_CONTRACT_REGISTRY_SCHEMA:
        errors.append("schema")
    seen: set[str] = set()
    for item in registry.get("contracts") or []:
        if not isinstance(item, dict):
            errors.append("contract_not_object")
            continue
        command = str(item.get("command") or "")
        if not command:
            errors.append("command")
        if command in seen:
            errors.append(f"duplicate:{command}")
        seen.add(command)
        if not str(item.get("schema") or "").startswith("rexecop."):
            errors.append(f"{command}:schema")
        exit_codes = item.get("exit_codes")
        if not isinstance(exit_codes, list) or not exit_codes:
            errors.append(f"{command}:exit_codes")
        elif not any(isinstance(code, dict) and code.get("code") == 0 for code in exit_codes):
            errors.append(f"{command}:exit_code_0")
        if item.get("redacted") is not True:
            errors.append(f"{command}:redacted")
        if item.get("bounded_output") is not True:
            errors.append(f"{command}:bounded_output")
    return errors
