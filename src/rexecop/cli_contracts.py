from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rexecop.cli_errors import CLI_ERROR_SCHEMA

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
    group: str
    formats: tuple[str, ...] = ("json",)
    default_format: str = "json"
    output_policy: str = "json_only"
    exit_codes: tuple[CliExitCode, ...] = (
        CliExitCode(0, "success"),
        CliExitCode(1, "validation_error_or_blocker"),
    )
    redacted: bool = True
    bounded_output: bool = True
    authority: str = "projection"
    error_schema: str = CLI_ERROR_SCHEMA
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": " ".join(self.command),
            "argv": list(self.command),
            "schema": self.schema,
            "stability": self.stability,
            "group": self.group,
            "formats": list(self.formats),
            "default_format": self.default_format,
            "output_policy": self.output_policy,
            "exit_codes": [item.as_dict() for item in self.exit_codes],
            "redacted": self.redacted,
            "bounded_output": self.bounded_output,
            "authority": self.authority,
            "error_schema": self.error_schema,
            "notes": list(self.notes),
        }


CLI_CONTRACTS: tuple[CliContract, ...] = (
    CliContract(
        command=("status",),
        schema="rexecop.operation_status.v0.1",
        stability="alpha_contract",
        group="operation_inspection",
    ),
    CliContract(
        command=("operation", "explain"),
        schema="rexecop.operation_explain.v0.1",
        stability="alpha_contract",
        group="operation_inspection",
    ),
    CliContract(
        command=("operation", "review"),
        schema="rexecop.operation_review.v0.1",
        stability="alpha_contract",
        group="operation_inspection",
        formats=("json", "table", "markdown"),
        output_policy="format_option",
    ),
    CliContract(
        command=("operation", "diff"),
        schema="rexecop.operation_plan_diff.v0.1",
        stability="alpha_contract",
        group="operation_inspection",
        formats=("json", "table", "markdown"),
        output_policy="format_option",
        exit_codes=(
            CliExitCode(0, "unchanged"),
            CliExitCode(1, "drifted_unavailable_or_validation_error"),
        ),
    ),
    CliContract(
        command=("receipt", "show"),
        schema="rexecop.receipt_show.v0.1",
        stability="alpha_contract",
        group="audit_inspection",
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
        group="audit_inspection",
        authority="runtime_evidence_projection",
    ),
    CliContract(
        command=("chain", "summary"),
        schema="rexecop.chain_summary.v0.1",
        stability="alpha_contract",
        group="audit_inspection",
        authority="digest_link_projection",
    ),
    CliContract(
        command=("chain", "explain"),
        schema="rexecop.chain_explain.v0.1",
        stability="alpha_contract",
        group="audit_inspection",
        authority="digest_link_projection",
        notes=("May include reaction replay status, but does not execute recovery.",),
    ),
    CliContract(
        command=("reaction", "explain"),
        schema="rexecop.reaction_explain.v0.1",
        stability="alpha_contract",
        group="audit_inspection",
        authority="sclite_reaction_chain_projection",
        notes=("Verifies persisted reaction-chain artifacts without starting a child operation.",),
    ),
    CliContract(
        command=("reaction-proposal-review",),
        schema="rexecop.proposal_review.v0.1",
        stability="alpha_contract",
        group="audit_inspection",
        authority="untrusted_proposal_projection",
        notes=("Does not execute, plan, or approve advisory proposal output.",),
    ),
    CliContract(
        command=("reaction-proposal-submit",),
        schema="rexecop.proposal_submission.v0.1",
        stability="alpha_contract",
        group="audit_inspection",
        authority="operator_review_record",
        notes=("Records accept_for_planning or reject without creating an operation.",),
    ),
    CliContract(
        command=("support", "bundle"),
        schema="rexecop.support_bundle.v0.1",
        stability="alpha_contract",
        group="audit_inspection",
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
        group="runtime_triage",
        output_policy="json_only_flag",
    ),
    CliContract(
        command=("runtime", "reconstruct-status"),
        schema="rexecop.runtime_reconstruction.v0.1",
        stability="alpha_contract",
        group="runtime_triage",
        output_policy="json_only_flag",
        authority="runtime_store_projection",
        notes=("Read-only reconstruction rules; does not execute recovery.",),
    ),
    CliContract(
        command=("ops",),
        schema="rexecop.ops.v0.1",
        stability="alpha_contract",
        group="runtime_triage",
        exit_codes=(
            CliExitCode(0, "no_blockers"),
            CliExitCode(1, "blockers_present_or_validation_error"),
        ),
    ),
    CliContract(
        command=("dead-letter", "list"),
        schema="rexecop.dead_letter_list.v0.1",
        stability="alpha_contract",
        group="runtime_triage",
    ),
    CliContract(
        command=("dead-letter", "show"),
        schema="rexecop.dead_letter_show.v0.1",
        stability="alpha_contract",
        group="runtime_triage",
    ),
    CliContract(
        command=("locks", "list"),
        schema="rexecop.locks_list.v0.1",
        stability="alpha_contract",
        group="runtime_triage",
    ),
    CliContract(
        command=("explain-error",),
        schema="rexecop.explain_error.v0.1",
        stability="alpha_contract",
        group="runtime_triage",
    ),
    CliContract(
        command=("governance", "controls"),
        schema="rexecop.governance_controls.v0.1",
        stability="alpha_contract",
        group="governance_inspection",
        exit_codes=(
            CliExitCode(0, "passed"),
            CliExitCode(1, "blocked_or_validation_error"),
        ),
        authority="govengine_projection",
        notes=(
            "Projects typed-execution control catalog; does not admit operations.",
        ),
    ),
    CliContract(
        command=("profile", "lint"),
        schema="rexecop.profile_conformance.v0.1",
        stability="alpha_contract",
        group="profile_developer",
        exit_codes=(
            CliExitCode(0, "passed"),
            CliExitCode(1, "failed_or_validation_error"),
        ),
        authority="profile_contract_projection",
    ),
    CliContract(
        command=("observability", "logs", "list"),
        schema="rexecop.structured_log_list.v0.1",
        stability="alpha_contract",
        group="observability",
        authority="runtime_observability_projection",
        notes=("Bounded structured logs with correlation and artifact refs.",),
    ),
    CliContract(
        command=("observability", "diagnostics"),
        schema="rexecop.runtime_diagnostics.v0.1",
        stability="alpha_contract",
        group="observability",
        authority="runtime_observability_projection",
        notes=("Uses the same failure classes as explain-error.",),
    ),
)

_OUTPUT_POLICIES = frozenset({"json_only", "json_only_flag", "format_option"})


def cli_contract_registry() -> dict[str, Any]:
    sorted_contracts = tuple(sorted(CLI_CONTRACTS, key=lambda item: item.command))
    contracts = [item.as_dict() for item in sorted_contracts]
    return {
        "schema": CLI_CONTRACT_REGISTRY_SCHEMA,
        "status": "present",
        "scope": "rexecop_operator_facing_cli",
        "contract_count": len(contracts),
        "contracts": contracts,
        "command_groups": _command_groups(sorted_contracts),
        "format_matrix": _format_matrix(sorted_contracts),
        "exit_code_matrix": _exit_code_matrix(sorted_contracts),
        "non_claims": [
            "Does not execute commands.",
            "Does not validate private runtime state.",
            "Does not make table or markdown formats stable unless explicitly listed per command.",
            "Does not replace command-specific tests.",
        ],
    }


def _command_groups(contracts: tuple[CliContract, ...]) -> list[dict[str, Any]]:
    groups: dict[str, list[str]] = {}
    for item in contracts:
        groups.setdefault(item.group, []).append(" ".join(item.command))
    return [
        {"group": group, "command_count": len(commands), "commands": commands}
        for group, commands in sorted(groups.items())
    ]


def _format_matrix(contracts: tuple[CliContract, ...]) -> list[dict[str, Any]]:
    return [
        {
            "command": " ".join(item.command),
            "group": item.group,
            "output_policy": item.output_policy,
            "formats": list(item.formats),
            "default_format": item.default_format,
        }
        for item in contracts
    ]


def _exit_code_matrix(contracts: tuple[CliContract, ...]) -> list[dict[str, Any]]:
    return [
        {
            "command": " ".join(item.command),
            "group": item.group,
            "exit_codes": [code.as_dict() for code in item.exit_codes],
            "error_schema": item.error_schema,
        }
        for item in contracts
    ]


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
        if not str(item.get("group") or ""):
            errors.append(f"{command}:group")
        if item.get("output_policy") not in _OUTPUT_POLICIES:
            errors.append(f"{command}:output_policy")
        formats = item.get("formats")
        if not isinstance(formats, list) or not formats:
            errors.append(f"{command}:formats")
        elif item.get("default_format") not in formats:
            errors.append(f"{command}:default_format")
        if item.get("error_schema") != CLI_ERROR_SCHEMA:
            errors.append(f"{command}:error_schema")
        exit_codes = item.get("exit_codes")
        if not isinstance(exit_codes, list) or not exit_codes:
            errors.append(f"{command}:exit_codes")
        elif not any(isinstance(code, dict) and code.get("code") == 0 for code in exit_codes):
            errors.append(f"{command}:exit_code_0")
        if item.get("redacted") is not True:
            errors.append(f"{command}:redacted")
        if item.get("bounded_output") is not True:
            errors.append(f"{command}:bounded_output")
    if not registry.get("command_groups"):
        errors.append("command_groups")
    if not registry.get("format_matrix"):
        errors.append("format_matrix")
    if not registry.get("exit_code_matrix"):
        errors.append("exit_code_matrix")
    return errors
