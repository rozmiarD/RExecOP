# Stack contract compatibility

This matrix records the source contract baseline that RExecOp `1.0.0rc1`
consumes. It is a compatibility guard, not a new source of truth.
Document id: `stack-contract-compatibility`.

## Ownership

| Layer | Owner | RExecOp dependency |
| --- | --- | --- |
| SCLite | Canonical lifecycle/evidence contracts, integrity, tickets, receipts and review-bundle verification | RExecOp emits and verifies compatible bundles, but does not own SCLite contract authority. |
| GovEngine | Governance: PolicyEngine, admission, obligations, constraints and enforcement-plan contracts | RExecOp consumes admission and policy-control projections, then enforces supported runtime controls. |
| RExecOp | Neutral execution mechanics plus observation, finding, reaction, escalation, trigger, watchdog and automation-chain runtime contracts | RExecOp owns runner/orchestration behavior without embedding profile/domain semantics. |
| Tecrax | Infrastructure profile semantics: intents, workflows, facts, findings, reactions, runbooks and connector contracts | RExecOp loads Tecrax as a profile package and treats its declarations as profile-owned data. |

## Package baseline

| Package | Public line | Required range in RExecOp | Role |
| --- | --- | --- | --- |
| `sclite-core` | `2.0.0` | `sclite-core==2.0.0` | Frozen SCLite lifecycle/evidence/review verification kernel; RExecOp owns reaction, trigger-decision, watchdog-decision and automation-chain schemas. |
| `govengine` | `1.0.0rc1` | `govengine==1.0.0rc1` | Governance facade plus explicitly classified adapter/module imports. The downstream import map and stack gate, not this prose, define the consumed surface. |
| `rexecop` | `1.0.0rc1` | current package | Stable read-only neutral runner, connectors, catalog and reaction mechanics. |
| `tecrax` | `0.4.0rc3` source candidate | external source consumer; no RExecOp package extra | Domain infrastructure profile tested through entry points and cross-repository fixtures. |

## Contract matrix

| Surface | Current contract | Owner | RExecOp use |
| --- | --- | --- | --- |
| SCLite lifecycle artifacts | `intent_contract.v0.2`, `policy_decision.v0.3`, `execution_contract.v0.3`, `execution_receipt.v0.2`, `evidence_contract.v0.2`, `artifact_chain_manifest.v0.2` | SCLite | Emitted on completion; scope provenance binds a GovEngine decision artifact to the exact operation target without claiming authority authentication. |
| SCLite scoped ticket | `execution_ticket.v0.3` | SCLite | Used for scoped dry-run/review bundle verification. |
| RExecOp reaction artifacts | `observation_envelope.v0.1`, `finding.v0.1`, `reaction_plan.v0.1`, `escalation_proposal.v0.1` and reaction-chain records | RExecOp | RExecOp owns schema resources and deterministic reaction mechanics; profiles own domain observation/finding meaning; SCLite supplies canonical verification machinery. |
| RExecOp trigger decision | `trigger_decision.v0.1` | RExecOp | Stores bounded trigger input, rule, governance admission and optional child-operation references. |
| RExecOp watchdog decision | `watchdog_decision.v0.1` | RExecOp | Stores bounded watchdog records, supervisor-action admission and affected runtime references. |
| RExecOp automation chain | `automation_chain.v0.1` | RExecOp | Projects child-operation nodes, edges, idempotency, budgets and recovery policy. Planning-only admission references never substitute for a signed execution decision. |
| GovEngine policy request/verdict | `govengine.policy` schema `v0.1` | GovEngine | Used for deterministic policy evaluation when an environment declares `policy_pack`. |
| GovEngine supported-contract catalog | `govengine.contract_compatibility` schema `v0.1`, `govengine-policy compatibility --json` | GovEngine | Consumed by RExecOp `doctor` and stack contract validators; unknown major contract versions fail closed. |
| GovEngine enforcement plan | `PolicyEnforcementPlan`, `RuntimeControlProjection`, existing `GovAdmissionDecision` binding | GovEngine | Consumed by RExecOp B2 before execution and at connector invoke. |
| GovEngine supervisor action admission | `SupervisorActionRequest`, `admit_supervisor_action()` | GovEngine | Admits bounded watchdog decisions over runtime refs and limits; GovEngine does not supervise workers or write artifacts. |
| GovEngine governed typed-execution admission | `typed_execution_governed_admission` schemas `v0.1` and `v0.2` (optional source surface) | GovEngine | Built-ins retain v0.1; an explicit policy-bound plugin shape selects v0.2. RExecOp verifies the composite, signed decision, capability/control bindings, approval and revocation before claiming one attempt and again before I/O. Absence does not block ordinary read-only compatibility, but configured mutation fails closed. |
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
| `mutation_ready` | false | The default `stable_read_only` runtime gate rejects `apply` / `recovery` before execution and again before built-in/plugin backend I/O; `doctor` blocks `lab_only`. | No stable apply/restart/configuration/VLAN/firewall/DNS/NTP mutation readiness. |

`alpha_readonly` is a retained machine-readable readiness label. It classifies
that compatibility track; it does not describe the maturity of the whole
`1.0.0rc1` package.

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
| Optional governed admission | Source integration is qualified against immutable GovEngine commit `9a78650a0e39524dcbf07d98f5fb71f89093fc66`. The public `govengine==1.0.0rc1` baseline need not expose v0.2; read-only use remains compatible and plugin mutation configuration fails closed when it is absent or incompatible. |
| RExecOp doctor | `rexecop doctor` emits `rexecop.doctor_report.v0.1` with `contract_versions`, all `blockers`, and the runtime-configuration `security_blockers` subset; `stack_contract_compatibility` remains fail-closed. |
| RExecOp explain | `rexecop operation explain` includes the same `contract_versions` summary for operator review. |
| SCLite artifact refs | RExecOp pins `SCLITE_SCHEMA_REFS` to supported `v0.x` artifact versions and validates them in `scripts/validate_stack_contracts.py`. |
| SCLite Python imports | The wheel-shipped `sclite.consumer_import_inventory.v1` allowlist is checked against `src/rexecop`; new top-level/deep imports and stale entries fail `validate_stack_contracts.py`. |
| Profile contract | Profiles declare `profile_contract.version` (`rexecop.profile_contract.v0.1`) for intent/workflow/governance surfaces. |

Golden fixture `tests/fixtures/stack_contract_compatibility_golden.json` guards
required and optional GovEngine surfaces, runtime projections and SCLite
artifact versions. Optional-surface absence is reported explicitly and does not
silently become a mutation-readiness claim.
`scripts/validate_cross_repo_golden_fixture.py` additionally gates the sanitized
Tecrax diagnosis flow through RExecOp reaction planning, GovEngine admission,
SCLite reaction-chain replay, `reaction explain`, `chain explain` and
idempotent recovery planning.

## Required gates

Release and compatibility changes must keep the relevant gates green:

- RExecOp: `scripts/validate_public_truth.py`, `scripts/validate_stack_contracts.py`,
  `scripts/validate_profile_conformance.py`, `scripts/validate_first_run_smoke.py`,
  `scripts/validate_operator_journeys.py`,
  `scripts/validate_cross_repo_golden_fixture.py`,
  `scripts/validate_stack_invariants.py`, `scripts/validate_external_review_gate.py`,
  `scripts/validate_release_train_preflight.py`, `scripts/validate_supply_chain_gate.py`,
  `scripts/validate_artifact_install_smoke.py`, `scripts/validate_clean_install_smoke.py`,
  `scripts/secret_scan.sh`, core-domain-token guard, `ruff`, `mypy src/rexecop`, and pytest.
- Tecrax: public truth, active profile validation, secret topology validation, `ruff`, `mypy src/tecrax`, and pytest.
- GovEngine: public truth, compatibility/security gates, `ruff`, `mypy
  govengine`, and pytest.
- SCLite: public truth, schema/security gates, `ruff`, `mypy`, and pytest.

The historical pre-1.0 claim-to-code qualification matrix is preserved in
[the archive](archive/pre-1.0-contract-qualification.md).
