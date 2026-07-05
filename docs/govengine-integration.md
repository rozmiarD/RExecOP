# GovEngine integration

RExecOp consumes GovEngine for **governance decisions only**. GovEngine defines and validates
admission envelopes and runner request/receipt contracts; it does **not** execute operations.

```text
Profile workflow
  -> RExecOp OperationPlan
  -> GovEngineRequest (from plan preview + operation context)
  -> GovEngineAdapter.evaluate()
  -> GovEngineDecision
  -> RExecOp state transition + evidence
  -> controlled execution when allowed (and approved if required)
```

## Adapters

| Adapter | Use |
| --- | --- |
| `GovEngineClient` | **Default** runtime path via `compose_runtime_admission_result()` |
| `StaticGovEngineAdapter` | Bootstrap and tests only — **not** production governance |

Factory: `default_govengine_adapter()` in `adapters/govengine_port/adapter.py`.

The static adapter is explicitly marked `bootstrap_only` and documented as non-production in
`static_adapter.py` and [safety-model.md](safety-model.md).

## Dependency

```text
govengine==0.16.9
```

Pinned compatible with the SCLite alpha line used by RExecOp (`sclite-core==1.0.8`).

## PolicyEngine integration

When the environment declares `policy_pack`, RExecOp:

1. Compiles the pack at `plan` and stores it on the operation.
2. Evaluates operation policy and asks GovEngine to build a digest-bound
   `PolicyEnforcementPlan` plus an existing `GovAdmissionDecision`.
3. Accepts plain `allow` or `allow_with_obligations` only when every returned control
   projects to the supported neutral runtime set.
4. Projects the operation verdict to `govengine_request_preview.policy_decision`
   (via `policy_verdict_to_gov_policy_decision()`).
5. Revalidates pack, verdict, admission, and digest at start/advance before backend IO.
6. Re-evaluates per connector at invoke time in `CompositeConnectorRuntime` before backends run.

Without `policy_pack`, `GovEngineClient` behavior is unchanged (compose inputs from preview overrides or fail-closed defaults).

Explain the same operation-shaped policy request without creating an operation
or executing connectors:

```bash
rexecop policy explain \
  --profile examples/profiles/runtime-fixture/profile.yaml \
  --env examples/environments/runtime-fixture.policy.example.yaml \
  --intent inspect_fixture_state \
  --target fixture-target \
  --mode dry_run
```

The command returns GovEngine `PolicyEvaluationExplanation` JSON under
`policy.explanation`. RExecOp supplies the bounded operation request shape and
does not compute matched rules, invariants, obligations, constraints, or
projected controls itself.

GovEngine side (G1):

```bash
govengine-policy explain policy.json request.json --json
govengine-policy simulate policy.json request.json --json
```

See GovEngine [POLICY_ENGINE.md](https://github.com/rozmiarD/GovEngine/blob/main/docs/POLICY_ENGINE.md).

Supported operation controls are:

- `receipt` / `receipt_required`: terminal internal receipt is mandatory;
- `output_digest_required`: each executed step receipt must have a bounded digest;
- `output_limit`: maximum serialized, redacted result bytes per step;
- `timeout`: tighter per-call limit for built-in shell, SSH, and HTTP connectors;
- `max_steps`: maximum declared workflow step count.

Unknown/malformed controls, timeout on an unsupported plugin backend, digest drift,
`approval_required`, and `deny` remain fail-closed. RExecOp does not infer that an
obligation is satisfied merely because it was returned. Connector-level policy controls
are not projected and remain blocked.

## Decision mapping

`RuntimeAdmissionResult` from GovEngine maps to RExecOp `GovEngineDecisionType`:

| GovEngine outcome | RExecOp effect (mutating modes) |
| --- | --- |
| `allowed` | May proceed to execution after approval state satisfied |
| `approval_required` | `waiting_for_approval` — no mutating connector calls |
| `blocked`, `read_only_only`, `human_required`, … | `blocked` or wait — no mutation |
| `error` / invalid admission | Fail closed |

For read-only workflows with `policy_pack`, the operation verdict and projected
controls are also validated before IO. Auto-approval of the read-only lifecycle
does not bypass PolicyEngine or enforcement-plan validation.

Evidence events: `govengine_decision_requested`, `govengine_decision_received`.

## Apply hard rule

Mutating modes (`apply`, `recovery`) require:

1. Positive GovEngine `allowed` decision recorded on the operation
2. Operation in `approved` state (manual `rexecop approve` when `approval_required`)
3. Connector-level check: `http_api` mutating actions also verify `mutating_allowed` at runtime

Read-only modes (`dry_run`, `observe`, `emergency_readonly`) auto-approve at start and refuse
mutating connector actions at the connector runtime layer.

## Runner contracts

`build_runner_request_preview()` materializes GovEngine runner request shapes from the
operation plan. Post-execution receipt binding uses GovEngine validation helpers where applicable.

## SCLite bridge

Admission metadata from `operation.metadata["govengine_admission"]` is bridged into SCLite
`policy_decision` and scoped ticket approval fields. Policy enforcement plan, admission,
pack, and verdict digest references are included in the SCLite execution contract and receipt.
SCLite computes and validates its own artifact descriptors; RExecOp does not claim SCLite
canonicalization ownership.

## Supervisor explanations (G2)

Watchdog and recovery triage may bind supervisor admission digests. RExecOp
`explain-error` includes GovEngine `SupervisorActionExplanation` when the ref
resolves to a watchdog record:

```bash
rexecop explain-error <watchdog-record-id>
```

GovEngine side (side-effect free, no recovery execution):

```bash
govengine-supervisor explain request.json --json
```

`explain_supervisor_action()` returns schema `v0.1` with `recovery_class`,
`gates_checked`, `reason_code`, `blockers`, and `safe_next_actions`. Digest-bound
`request_digest` and `admission_digest` align with `admit_supervisor_action()`.

See [runtime-recovery-ops.md](runtime-recovery-ops.md) and GovEngine
[RUNTIME_ADMISSION.md](https://github.com/rozmiarD/GovEngine/blob/main/docs/RUNTIME_ADMISSION.md#supervisor-action-explanation).

## Automation transition admission

Reaction-planned child operations are projected into SCLite
`automation_chain.v0.1` artifacts. RExecOp owns runtime projection and
child-operation plan mechanics; SCLite owns the chain artifact shape; GovEngine
owns automation-transition admission when the installed GovEngine line exposes:

- `AutomationTransitionRequest`
- `admit_automation_transition()`
- `automation_transition_request_digest()`
- `automation_transition_admission_digest()`
- `explain_automation_transition()`

When those contracts are present, RExecOp records the request digest,
admission digest and redacted explanation in the reaction admission binding and
writes the GovEngine admission digest onto the `admitted_child` edge. When the
current installed GovEngine does not expose the unreleased automation API,
RExecOp reports the binding as `unavailable` and does not claim a GovEngine
automation admission digest.

## Profile governance (G3)

Profile developer surfaces attach GovEngine compatibility output without
reimplementing policy reasoning in RExecOp core:

```bash
rexecop profiles show tecrax --track readonly
```

`profiles show` and `run_profile_developer_check()` include `govengine_governance`
with `ProfileGovernanceProjection` and `ProfileConnectorCompatibilityReport`
digests. GovEngine side:

```bash
govengine-policy profile-governance projection.json --json
```

See [profile-developer-surface.md](profile-developer-surface.md) and GovEngine
[PROFILE_GOVERNANCE.md](https://github.com/rozmiarD/GovEngine/blob/main/docs/PROFILE_GOVERNANCE.md).

## Boundary

GovEngine validates/contracts admission and runner records. RExecOp remains the runner,
orchestrator, and executor that invokes profile-declared workflow steps and connectors.
