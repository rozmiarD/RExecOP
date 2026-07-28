"""Machine-readable candidate public API for the RExecOp 1.x line."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rexecop import __version__
from rexecop.cli_contracts import CLI_CONTRACTS

PUBLIC_API_SCHEMA = "rexecop.public_api.v1"
PUBLIC_API_STABILITY = "stable_v1"
CLI_ALPHA_STABILITY = "alpha"
RUNTIME_ROOT_UPGRADE_POLICY = "alpha_root_requires_new_v1_root"


@dataclass(frozen=True)
class PublicImport:
    module: str
    symbols: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"module": self.module, "symbols": list(self.symbols)}


SUPPORTED_PUBLIC_IMPORTS: tuple[PublicImport, ...] = (
    PublicImport("rexecop", ("__version__",)),
    PublicImport(
        "rexecop.connectors",
        ("ConnectorRequest", "ConnectorResponse", "ConnectorRuntime"),
    ),
    PublicImport(
        "rexecop.contracts.orchestration",
        (
            "OWNER_SCHEMA_REFS",
            "ORCHESTRATION_SCHEMA_RESOLVER",
            "automation_edge",
            "automation_node",
            "build_automation_chain",
            "build_finding",
            "build_observation_envelope",
            "build_reaction_chain_manifest",
            "build_reaction_plan",
            "build_trigger_decision",
            "build_watchdog_decision",
            "reaction_idempotency_key",
            "trigger_decision_descriptor",
            "trigger_decision_digest",
            "validate_escalation_proposal",
            "verify_automation_chain",
            "verify_owner_artifact",
            "verify_reaction_chain_manifest",
        ),
    ),
    PublicImport("rexecop.evidence", ("EvidenceEventType", "redact_payload")),
    PublicImport(
        "rexecop.execution",
        (
            "STEP_EXECUTION_SPEC_SCHEMA",
            "TYPED_EXECUTION_BINDING_SCHEMA",
            "ExecutionPolicyBinding",
            "ExecutionReceipt",
            "ExecutionRequest",
            "ExecutionStep",
            "ExecutionStepReceipt",
            "ResourceLimits",
            "StepExecutionContext",
            "StepExecutionResult",
            "StepExecutor",
            "build_typed_execution_binding",
            "compile_step_execution_spec",
            "execution_receipt_digest",
            "execution_request_digest",
            "step_execution_spec_digest",
        ),
    ),
    PublicImport(
        "rexecop.profile",
        (
            "LoadedProfile",
            "list_registered_profiles",
            "load_profile",
            "resolve_profile_path",
            "validate_profile_contract",
        ),
    ),
    PublicImport(
        "rexecop.reaction",
        (
            "ReactionContext",
            "ReactionEvaluation",
            "ReactionPack",
            "compile_reaction_pack",
            "evaluate_reaction",
        ),
    ),
    PublicImport(
        "rexecop.errors",
        (
            "RExecOpConcurrencyConflict",
            "RExecOpError",
            "RExecOpGovernanceDecisionError",
            "RExecOpLeaseLost",
            "RExecOpMutationNotCertified",
            "RExecOpOutcomeIndeterminate",
            "RExecOpStateError",
            "RExecOpUnsafeDestination",
            "RExecOpValidationError",
        ),
    ),
    PublicImport(
        "rexecop.public_api",
        ("PUBLIC_API_SCHEMA", "SUPPORTED_PUBLIC_IMPORTS", "public_api_manifest"),
    ),
)

# Every installed CLI leaf not covered by CLI_CONTRACTS must remain explicit here.
# These commands are available in the current candidate, but their output/API is not
# part of the supported 1.x compatibility promise.
ALPHA_CLI_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("action", "configure"),
    ("action", "diff"),
    ("action", "list"),
    ("action", "policy-preview"),
    ("action", "preview"),
    ("action", "show"),
    ("action", "templates", "list"),
    ("action", "validate"),
    ("approve",),
    ("backup", "create"),
    ("backup", "restore"),
    ("cancel",),
    ("capabilities", "list"),
    ("connectors", "list"),
    ("connectors", "show"),
    ("contracts", "cli"),
    ("doctor",),
    ("env", "lint"),
    ("escalate",),
    ("examples", "materialize"),
    ("history",),
    ("init",),
    ("operation", "truth-path"),
    ("operations", "explain"),
    ("operations", "list"),
    ("operations", "unavailable"),
    ("pause",),
    ("plan",),
    ("policy", "explain"),
    ("profile", "harness"),
    ("profile", "manifest"),
    ("profiles", "list"),
    ("profiles", "show"),
    ("queue",),
    ("reaction-plan",),
    ("reaction-proposal-validate",),
    ("reaction-replay",),
    ("reaction-start",),
    ("resume",),
    ("retry",),
    ("rollback",),
    ("runbook", "show"),
    ("runtime", "recover"),
    ("secrets", "doctor"),
    ("secrets", "suggest-ref"),
    ("start",),
    ("targets", "list"),
    ("targets", "show"),
    ("trigger",),
    ("validate",),
    ("version",),
    ("watchdog", "manual-record"),
    ("worker", "run"),
)


def public_api_manifest() -> dict[str, Any]:
    stable_cli = sorted(" ".join(item.command) for item in CLI_CONTRACTS)
    alpha_cli = sorted(" ".join(item) for item in ALPHA_CLI_COMMANDS)
    return {
        "schema": PUBLIC_API_SCHEMA,
        "rexecop_version": __version__,
        "python_api": {
            "stability": PUBLIC_API_STABILITY,
            "imports": [item.as_dict() for item in SUPPORTED_PUBLIC_IMPORTS],
        },
        "cli": {
            "stable_commands": stable_cli,
            "alpha_commands": alpha_cli,
            "internal_commands": [],
        },
        "schema_compatibility_policy": "unknown_major_fail_closed",
        "runtime_root_upgrade_policy": RUNTIME_ROOT_UPGRADE_POLICY,
        "non_claims": [
            "Modules and symbols absent from python_api.imports are internal or alpha.",
            "Alpha CLI commands carry no 1.x output compatibility promise.",
            "This manifest does not certify production or mutation readiness.",
        ],
    }


__all__ = [
    "ALPHA_CLI_COMMANDS",
    "CLI_ALPHA_STABILITY",
    "PUBLIC_API_SCHEMA",
    "PUBLIC_API_STABILITY",
    "RUNTIME_ROOT_UPGRADE_POLICY",
    "SUPPORTED_PUBLIC_IMPORTS",
    "PublicImport",
    "public_api_manifest",
]
