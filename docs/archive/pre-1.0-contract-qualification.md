# Pre-1.0 contract qualification

This document preserves the historical claim-to-code matrix used while the
RExecOp 1.0 public surface was being qualified. It is release provenance, not
the current compatibility contract.

Current compatibility requirements live in
[`docs/stack-contract-compatibility.md`](../stack-contract-compatibility.md).

## Historical M8 claim-to-code matrix

| Public claim | Code / schema anchor | Validator / test at qualification time |
| --- | --- | --- |
| CLI registry | `rexecop.cli_contract_registry.v0.1` | `tests/test_cli_contracts.py`, `validate_public_truth.py` |
| CLI error envelope | `rexecop.cli_error.v0.1` | `tests/test_cli_errors.py` |
| Structured logs | `rexecop.structured_log_event.v0.1` | `tests/test_observability.py` |
| Runtime diagnostics | `rexecop.runtime_diagnostics.v0.1` | `tests/test_observability.py` |
| Runtime reconstruction | `rexecop.runtime_reconstruction.v0.1` | `tests/test_runtime_recovery.py` |
| Advisory proposal review | `rexecop.proposal_review.v0.1`, `rexecop.proposal_submission.v0.1` | `tests/test_reaction_interpreter.py` |
| Typed execution truth path | `project_truth_path()`, `admit_typed_execution()` | artifact- and clean-install smoke gates |
| Cross-repository fixture | `rexecop.reaction_explain.v0.1`, `rexecop.chain_explain.v0.1` | `validate_cross_repo_golden_fixture.py` |
| Operator journeys | read-only, failure, governance and audit CLI journeys | `validate_operator_journeys.py` |
| Governance controls CLI | `rexecop.governance_controls.v0.1` | operator journey and CLI tests |
| Stack invariants | invariant test marker | `validate_stack_invariants.py` |
| Release/process gates | preflight, public-index, supply-chain and external-review validators | release workflow and qualification script |
