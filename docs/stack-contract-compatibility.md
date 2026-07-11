# Stack contract compatibility

This matrix records the source contract baseline that RExecOp `0.2.25a0`
consumes. It is a compatibility guard, not a new source of truth.
Document id: `stack-contract-compatibility`.

## Ownership

| Layer | Owner | RExecOp dependency |
| --- | --- | --- |
| SCLite | Truth: artifacts, schemas, evidence, receipts, review bundles and reaction-chain records | RExecOp validates and emits compatible artifacts, but does not own SCLite schema authority. |
| GovEngine | Governance: PolicyEngine, admission, obligations, constraints and enforcement-plan contracts | RExecOp consumes admission and policy-control projections, then enforces supported runtime controls. |
| RExecOp | Neutral execution mechanics: lifecycle, connectors, catalog, queue, worker, reactions and receipts | RExecOp owns runner behavior without embedding profile/domain semantics. |
| Tecrax | Infrastructure profile semantics: intents, workflows, facts, findings, reactions, runbooks and connector contracts | RExecOp loads Tecrax as a profile package and treats its declarations as profile-owned data. |

## Package baseline

| Package | Public line | Required range in RExecOp | Role |
| --- | --- | --- | --- |
| `sclite-core` | `1.1.0rc1` | `sclite-core==1.1.0rc1` | SCLite truth, reaction, trigger-decision, watchdog-decision and automation-chain artifact schemas. |
| `govengine` | `0.16.12rc1` | `govengine==0.16.12rc1` | PolicyEngine MVP, B2 enforcement-plan contracts, trigger-planning admission, supervisor-action admission, supervisor explanations and automation-transition admission. |
| `rexecop` | `0.2.25a0` | current package | Neutral runner, connectors, catalog and reaction mechanics. |
| `tecrax` | `0.3.22a0` | `tecrax==0.3.22a0` via optional extra | Domain infrastructure profile. |

## Contract matrix

| Surface | Current contract | Owner | RExecOp use |
| --- | --- | --- | --- |
| SCLite lifecycle artifacts | `intent_contract.v0.2`, `policy_decision.v0.2`, `execution_contract.v0.2`, `execution_receipt.v0.2`, `evidence_contract.v0.2`, `artifact_chain_manifest.v0.2` | SCLite | Emitted on completion through the SCLite adapter. |
| SCLite scoped ticket | `execution_ticket.v0.3` | SCLite | Used for scoped dry-run/review bundle truth. |
| SCLite reaction artifacts | `observation_envelope.v0.1`, `finding.v0.1`, `reaction_plan.v0.1`, `escalation_proposal.v0.1`, reaction chain manifest | SCLite | Validated/emitted as artifacts; RExecOp does not own domain observation meaning. |
| SCLite trigger decision artifact | `trigger_decision.v0.1` | SCLite | Stores bounded trigger event, rule, GovEngine admission and optional child-operation references; RExecOp remains the trigger planner. |
| SCLite watchdog decision artifact | `watchdog_decision.v0.1` | SCLite | Stores bounded watchdog record, supervisor-action admission and affected runtime references; RExecOp remains the runtime supervisor. |
| SCLite automation chain contract | `automation_chain.v0.1` | SCLite | RExecOp emits child-operation chain projections with nodes, edges, edge idempotency, depth/reaction budgets, recovery policy and LLM proposal-only invariants. GovEngine automation admission refs are embedded through the required GovEngine `0.16.12rc1` automation-transition contract. |
| GovEngine policy request/verdict | `govengine.policy` schema `v0.1` | GovEngine | Used for deterministic policy evaluation when an environment declares `policy_pack`. |
| GovEngine supported-contract catalog | `govengine.contract_compatibility` schema `v0.1`, `govengine-policy compatibility --json` | GovEngine | Consumed by RExecOp `doctor` and stack contract validators; unknown major contract versions fail closed. |
| GovEngine enforcement plan | `PolicyEnforcementPlan`, `RuntimeControlProjection`, existing `GovAdmissionDecision` binding | GovEngine | Consumed by RExecOp B2 before execution and at connector invoke. |
| GovEngine supervisor action admission | `SupervisorActionRequest`, `admit_supervisor_action()` | GovEngine | Admits bounded watchdog decisions over runtime refs and limits; GovEngine does not supervise workers or write artifacts. |
| RExecOp execution records | `ExecutionRequest` / `ExecutionReceipt` schema `v0.2` | RExecOp | Stored in workflow `shared_state` and bound to policy digests. |
| RExecOp policy pack lifecycle | `rexecop.policy_pack_lifecycle.v0.1` | RExecOp/GovEngine | RExecOp projects absent/compiled/bound/enforcement stages; GovEngine owns compilation, reasoning and pack digests. |
| RExecOp reaction mechanics | compiled profile reaction pack, `ReactionContext`, `ReactionService`, replayable reaction chain | RExecOp | Deterministic evaluation and child-operation planning mechanics only. |
| RExecOp profile conformance | `validate_profile_conformance()` and `scripts/validate_profile_conformance.py --track readonly` | RExecOp | Verifies profile-declared read-only operation/catalog/reaction-observation contracts without importing domain semantics. Mutation candidates are reported on a separate track and do not widen the read-only readiness claim. |
| RExecOp profile contract | `rexecop.profile_contract.v0.1` (`profile_contract.version`) | RExecOp/Tecrax | Profiles declare contract version and required governance sections; conformance gates fail closed on missing version. |
| RExecOp runtime projections | `rexecop.stack_contract_compatibility.v0.1` matrix | RExecOp | Covers typed execution specs, execution request/receipt, action configure/preview CLI JSON, runtime manifest and doctor/explain outputs. |
| RExecOp catalog mechanics | target catalog and profile-derived operation descriptors | RExecOp | Applicability projection and drift rejection, never authorization. |
| Tecrax host facts | `tecrax.basic_host_inventory@1.0`, `tecrax.ntp_local_health@1.0`, `tecrax.docker_service_health@1.0`, `tecrax.host_security_posture@1.0`, `tecrax.ntp_server_observation@1.0` | Tecrax | Profile-owned facts consumed as bounded workflow outputs. |
| Tecrax service/API facts | `tecrax.zabbix_api_reachability@1.0`, `tecrax.zabbix_problem_summary@1.0`, `tecrax.zabbix_host_availability_summary@1.0`, `tecrax.adguard_reachability@1.0`, `tecrax.portainer_reachability@1.0` | Tecrax | Read-only infrastructure summaries with secrets outside repositories. |
| Tecrax aggregate diagnosis | `tecrax.monitoring_host_diagnosis@1.0` | Tecrax | Domain diagnosis and finding source for reaction rules. |
| Tecrax network facts | `tecrax.network_device_inventory@1.0`, `tecrax.network_management_posture@1.0` | Tecrax | Read-only legacy network-device inventory through an operator adapter. |

## Readiness labels

| Label | Status | Evidence | Non-claim |
| --- | --- | --- | --- |
| `alpha_readonly` | active | Published stack installs from PyPI, read-only Tecrax profile slices, bounded evidence and receipts. | Not production readiness. |
| `deterministic_plan_only` | active | Operation planning, catalog applicability, manual reaction planning and opt-in `auto_react=plan_only` are deterministic. | Does not auto-start child operations. |
| `deterministic_execute_readonly` | active | Allowlisted `ssh_readonly`, `local_shell_readonly`, generic `http_api`, PolicyEngine gates and SCLite receipt emission. | Does not authorize mutation or unattended operations. |
| `advisory_llm` | planned only | SCLite `escalation_proposal.v0.1` exists and Tecrax can produce bounded untrusted proposals. | No LLM provider, no LLM execution authority, no secrets to LLM. |
| `mutation_ready` | false | Mutating controls remain blocked by policy/admission/operator gates. | No apply/restart/configuration/VLAN/firewall/DNS/NTP mutation readiness. |

`scripts/validate_profile_conformance.py` defaults to `--track readonly`. The
separate `--track mutation` report is allowed to discover and validate bounded
mutation candidates such as Tecrax `configure_chrony_ntp_server`, but that report
is not a `mutation_ready` claim and does not authorize execution.

## Compatibility policy

Stack hosts must treat contract version drift as a release gate, not as a silent
runtime behavior change. Compatibility policy id: `unknown_major_fail_closed`.

| Rule | Behavior |
| --- | --- |
| Unknown major version | Fail closed before execution planning or backend IO. |
| Unknown minor/patch within supported major | Fail closed until the host explicitly pins the version. |
| GovEngine catalog | `govengine-policy compatibility --json` is the machine-readable supported-contract report. |
| RExecOp doctor | `rexecop doctor` emits `rexecop.doctor_report.v0.1` with `contract_versions` and blocker `stack_contract_compatibility`. |
| RExecOp explain | `rexecop operation explain` includes the same `contract_versions` summary for operator review. |
| SCLite artifact refs | RExecOp pins `SCLITE_SCHEMA_REFS` to supported `v0.x` artifact versions and validates them in `scripts/validate_stack_contracts.py`. |
| SCLite Python imports | The wheel-shipped `sclite.consumer_import_inventory.v1` allowlist is checked against `src/rexecop`; new top-level/deep imports and stale entries fail `validate_stack_contracts.py`. |
| Profile contract | Profiles declare `profile_contract.version` (`rexecop.profile_contract.v0.1`) for intent/workflow/governance surfaces. |

Golden fixture `tests/fixtures/stack_contract_compatibility_golden.json` guards
required GovEngine surfaces, runtime projections and SCLite artifact versions.
`scripts/validate_cross_repo_golden_fixture.py` additionally gates the sanitized
Tecrax diagnosis flow through RExecOp reaction planning, GovEngine admission,
SCLite reaction-chain replay, `reaction explain`, `chain explain` and
idempotent recovery planning.

## M8 claim-to-code matrix

| Public claim | Code / schema anchor | Validator / test |
| --- | --- | --- |
| `contracts cli` registry | `rexecop.cli_contract_registry.v0.1` | `tests/test_cli_contracts.py`, `validate_public_truth.py` |
| CLI error envelope | `rexecop.cli_error.v0.1` | `tests/test_cli_errors.py`, registry `error_schema` |
| Structured logs | `rexecop.structured_log_event.v0.1` | `tests/test_observability.py`, `observability/logs list` |
| Runtime diagnostics | `rexecop.runtime_diagnostics.v0.1` | `tests/test_observability.py`, `observability diagnostics` |
| Runtime-store reconstruction | `rexecop.runtime_reconstruction.v0.1` | `tests/test_runtime_recovery.py`, `runtime reconstruct-status --json` |
| Advisory proposal review | `rexecop.proposal_review.v0.1`, `rexecop.proposal_submission.v0.1` | `tests/test_reaction_interpreter.py`, CLI registry/error tests |
| M6/M7 typed execution + truth-path | `project_truth_path()`, `admit_typed_execution()` | `validate_artifact_install_smoke.py`, `validate_clean_install_smoke.py` |
| Cross-repo golden fixture | `rexecop.reaction_explain.v0.1`, `rexecop.chain_explain.v0.1` | `scripts/validate_cross_repo_golden_fixture.py` |
| Operator journey §6 | `validate_operator_journeys.py` (read-only, failure, governance, audit CLI) | CI, `scripts/run_alpha_signoff_checks.sh`, `tests/test_operator_journeys.py` |
| Governance controls CLI | `rexecop.governance_controls.v0.1` | `rexecop governance controls`, `tests/test_operator_journeys.py`, GovEngine catalog consumption |
| M8.5 stack invariants | `pytest -m invariant`, `validate_stack_invariants.py` | `tests/test_stack_invariants.py` |
| M8.5 release/process gates | `validate_release_train_preflight.py`, `validate_public_index_release_smoke.py`, `validate_supply_chain_gate.py`, `validate_external_review_gate.py` | CI `publish.yml`, alpha sign-off |

## Required gates

The stack must keep these gates green before implementing later automation:

- RExecOp: `scripts/validate_public_truth.py`, `scripts/validate_stack_contracts.py`,
  `scripts/validate_profile_conformance.py`, `scripts/validate_first_run_smoke.py`,
  `scripts/validate_operator_journeys.py`,
  `scripts/validate_cross_repo_golden_fixture.py`,
  `scripts/validate_stack_invariants.py`, `scripts/validate_external_review_gate.py`,
  `scripts/validate_release_train_preflight.py`, `scripts/validate_supply_chain_gate.py`,
  `scripts/validate_artifact_install_smoke.py`, `scripts/validate_clean_install_smoke.py`,
  `scripts/secret_scan.sh`, core-domain-token guard, `ruff`, `mypy src/rexecop`, and pytest.
- Tecrax: public truth, active profile validation, secret topology validation, `ruff`, `mypy src/tecrax`, and pytest.
- GovEngine: public truth, alpha readiness, `ruff`, `mypy govengine`, and pytest.
- SCLite: public truth, schema/security gates, `ruff`, `mypy`, and pytest.
