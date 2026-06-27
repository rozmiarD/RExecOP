# Stack contract compatibility

This matrix records the public contract baseline that RExecOp `0.2.7a0`
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
| `sclite-core` | `1.0.5` | `sclite-core>=1.0.5,<1.1` | SCLite truth and reaction artifact schemas. |
| `govengine` | `0.16.1` | `govengine>=0.16.1,<0.17` | PolicyEngine MVP plus B2 enforcement-plan contracts. |
| `rexecop` | `0.2.7a0` | current package | Neutral runner, connectors, catalog and reaction mechanics. |
| `tecrax` | `0.3.6a0` | `tecrax>=0.3.6a0,<0.4` via optional extra | Domain infrastructure profile. |

## Contract matrix

| Surface | Current contract | Owner | RExecOp use |
| --- | --- | --- | --- |
| SCLite lifecycle artifacts | `intent_contract.v0.2`, `policy_decision.v0.2`, `execution_contract.v0.2`, `execution_receipt.v0.2`, `evidence_contract.v0.2`, `artifact_chain_manifest.v0.2` | SCLite | Emitted on completion through the SCLite adapter. |
| SCLite scoped ticket | `execution_ticket.v0.3` | SCLite | Used for scoped dry-run/review bundle truth. |
| SCLite reaction artifacts | `observation_envelope.v0.1`, `finding.v0.1`, `reaction_plan.v0.1`, `escalation_proposal.v0.1`, reaction chain manifest | SCLite | Validated/emitted as artifacts; RExecOp does not own domain observation meaning. |
| GovEngine policy request/verdict | `govengine.policy` schema `v0.1` | GovEngine | Used for deterministic policy evaluation when an environment declares `policy_pack`. |
| GovEngine enforcement plan | `PolicyEnforcementPlan`, `RuntimeControlProjection`, existing `GovAdmissionDecision` binding | GovEngine | Consumed by RExecOp B2 before execution and at connector invoke. |
| RExecOp execution records | `ExecutionRequest` / `ExecutionReceipt` schema `v0.2` | RExecOp | Stored in workflow `shared_state` and bound to policy digests. |
| RExecOp reaction mechanics | compiled profile reaction pack, `ReactionContext`, `ReactionService`, replayable reaction chain | RExecOp | Deterministic evaluation and child-operation planning mechanics only. |
| RExecOp catalog mechanics | target catalog and profile-derived operation descriptors | RExecOp | Applicability projection and drift rejection, never authorization. |
| Tecrax host facts | `tecrax.basic_host_inventory@1.0`, `tecrax.ntp_local_health@1.0`, `tecrax.docker_service_health@1.0`, `tecrax.host_security_posture@1.0`, `tecrax.ntp_server_observation@1.0` | Tecrax | Profile-owned facts consumed as bounded workflow outputs. |
| Tecrax service/API facts | `tecrax.zabbix_api_reachability@1.0`, `tecrax.zabbix_problem_summary@1.0`, `tecrax.zabbix_host_availability_summary@1.0`, `tecrax.adguard_reachability@1.0`, `tecrax.portainer_reachability@1.0` | Tecrax | Read-only infrastructure summaries with secrets outside repositories. |
| Tecrax aggregate diagnosis | `tecrax.monitoring_host_diagnosis@1.0` | Tecrax | Domain diagnosis and finding source for reaction rules. |
| Tecrax network facts | `tecrax.network_device_inventory@1.0`, `tecrax.network_management_posture@1.0` | Tecrax | Read-only legacy network-device inventory through an operator adapter. |

## Readiness labels

| Label | Status | Evidence | Non-claim |
| --- | --- | --- | --- |
| `alpha_readonly` | active | Published stack installs from PyPI, read-only Tecrax profile slices, bounded evidence and receipts. | Not production readiness. |
| `deterministic_plan_only` | active | Operation planning, catalog applicability and manual reaction planning are deterministic. | Does not auto-start child operations. |
| `deterministic_execute_readonly` | active | Allowlisted `ssh_readonly`, `local_shell_readonly`, generic `http_api`, PolicyEngine gates and SCLite receipt emission. | Does not authorize mutation or unattended operations. |
| `advisory_llm` | planned only | SCLite `escalation_proposal.v0.1` exists and Tecrax can produce bounded untrusted proposals. | No LLM provider, no LLM execution authority, no secrets to LLM. |
| `mutation_ready` | false | Mutating controls remain blocked by policy/admission/operator gates. | No apply/restart/configuration/VLAN/firewall/DNS/NTP mutation readiness. |

## Required gates

The stack must keep these gates green before implementing later automation:

- RExecOp: `scripts/validate_public_truth.py`, `scripts/validate_stack_contracts.py`,
  `scripts/secret_scan.sh`, core-domain-token guard, `ruff`, `mypy src/rexecop`, and pytest.
- Tecrax: public truth, active profile validation, secret topology validation, `ruff`, `mypy src/tecrax`, and pytest.
- GovEngine: public truth, alpha readiness, `ruff`, `mypy govengine`, and pytest.
- SCLite: public truth, schema/security gates, `ruff`, `mypy`, and pytest.
