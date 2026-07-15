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
govengine==0.17.0rc2
```

Pinned compatible with the final frozen SCLite 2.0 verifier used by RExecOp (`sclite-core==2.0.0`).

## PolicyEngine integration

When the environment declares `policy_pack`, RExecOp:

1. Compiles the pack at `plan` and stores it on the operation.
2. Records `rexecop.policy_pack_lifecycle.v0.1` with GovEngine-owned
   `policy_pack_digest`, lifecycle stages and enforcement binding digests.
3. Evaluates operation policy and asks GovEngine to build a digest-bound
   `PolicyEnforcementPlan` plus an existing `GovAdmissionDecision`.
4. Accepts plain `allow` or `allow_with_obligations` only when every returned control
   projects to the supported neutral runtime set.
5. Projects the operation verdict to `govengine_request_preview.policy_decision`
   (via `policy_verdict_to_gov_policy_decision()`).
6. Revalidates pack, verdict, admission, and digest at start/advance before backend IO.
7. Re-evaluates per connector at invoke time in `CompositeConnectorRuntime` before backends run.

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
`policy.explanation` and the RExecOp lifecycle projection under
`policy.lifecycle`. RExecOp supplies the bounded operation request shape and does
not compute matched rules, invariants, obligations, constraints, pack digests, or
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

## Typed-execution boundary hardening

For each connector step, RExecOp projects the bounded runtime capability record
and asks GovEngine to compute/validate the digest of that projection. The
RExecOp source descriptor keeps its own runtime digest; the two digests are not
interchangeable because they cover different records.

Operation capability requirements come only from the owning profile connector
contract (`required_capability_descriptors` or its explicit backend/action
variant) or from an explicit governance overlay/caller argument. RExecOp does
not fall back to capabilities declared by the selected backend. Missing
requirements fail closed in GovEngine as
`operation_capability_requirements_missing`.

`no_network` remains the default fixture path. Outbound network/destination
admission additionally requires a separate digest-bound network policy
projection declared by the profile (`network_policy_binding`, optionally
selected by backend). RExecOp does not derive allowed schemes, address classes,
or origin constraints from the requested destination.

## Decision mapping

`RuntimeAdmissionResult` from GovEngine maps to RExecOp `GovEngineDecisionType`:

| GovEngine outcome | RExecOp effect (mutating modes) |
| --- | --- |
| `allowed` | May proceed only after all current typed-execution and approval gates remain satisfied |
| `approval_required` | `waiting_for_approval` — no mutating connector calls |
| `blocked`, `read_only_only`, `human_required`, … | `blocked` or wait — no mutation |
| `error` / invalid admission | Fail closed |

For read-only workflows with `policy_pack`, the operation verdict and projected
controls are also validated before IO. Auto-approval of the read-only lifecycle
does not bypass PolicyEngine or enforcement-plan validation.

Evidence events: `govengine_decision_requested`, `govengine_decision_received`.

## Canonical attempt decision boundary

The pre-I/O canonical path is configured with four host-owned inputs on
`OperationController`: `AttemptGovernanceAuthority`, `VerifierPort`, `SigningPolicy`,
and `TrustPolicy`. Partial configuration is rejected. For every connector attempt,
RExecOp:

1. preallocates `attempt_id`;
2. projects current runtime instance, lease epoch, hashed lease/fencing bindings,
   execution and payload digests, requested-scope digest, and connector inventory epoch;
3. asks the authority for a signed GovEngine `GovernanceDecision`;
4. verifies the signed record and exact runtime bindings;
5. atomically consumes both decision digest and nonce;
6. issues `rexecop.runtime_attempt_permit.v0.1` and persists `attempt started` before IO.

RExecOp never evaluates policy or signs the decision. The authority/signer/verifier and
trust anchors remain host-owned. A mutating connector attempt without this complete
configuration fails before an attempt journal record is created. Existing read-only
execution may use the explicit `legacy_read_only` compatibility binding; it is not a
signed-decision claim.

After governed connector I/O, RExecOp builds `RuntimeReceiptBinding v1` from the
claimed decision, immutable runtime permit, exact attempt/lease/fencing/inventory
facts and bounded output metrics. GovEngine recomputes that binding and returns
`ReceiptConformanceResult v1`. Nonconformance fails the workflow even when the
connector call itself succeeded. The result is a governance postcondition check,
not a SCLite receipt or proof that a compromised runtime reported honest facts.

## Apply hard rule

Mutating modes (`apply`, `recovery`) require:

1. Positive GovEngine `allowed` decision recorded on the operation
2. Operation in `approved` state (manual `rexecop approve` when `approval_required`)
3. Connector-level check: `http_api` mutating actions also verify `mutating_allowed` at runtime
4. A trusted signed canonical `GovernanceDecision`, atomically claimed for the exact attempt

Read-only modes (`dry_run`, `observe`, `emergency_readonly`) auto-approve at start and refuse
mutating connector actions at the connector runtime layer.

## Runner contracts

`build_runner_request_preview()` materializes GovEngine runner request shapes from the
operation plan. Post-execution receipt binding uses GovEngine validation helpers where applicable.

## SCLite bridge

Admission metadata from `operation.metadata["govengine_admission"]` is bridged into SCLite
`policy_decision` and scoped ticket approval fields. Policy enforcement plan, admission,
pack, and verdict digest references are included in the SCLite execution contract and receipt.
Governed step receipt bindings and conformance results are projected under
`rexecop_runtime_binding.governance_bindings` without changing the frozen SCLite schema.
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

Trigger, supervisor and automation admission records are planning-only
adapters. Their metadata carries
`governance_flow=planning_admission_adapter.v1` and
`execution_authority=false`; these records must not be supplied where a signed,
attempt-bound `GovernanceDecision` is required. If planning creates an
executable operation, the normal RExecOp pre-I/O path still obtains, verifies
and atomically claims the canonical decision.

See [runtime-recovery-ops.md](runtime-recovery-ops.md) and GovEngine
[RUNTIME_ADMISSION.md](https://github.com/rozmiarD/GovEngine/blob/main/docs/RUNTIME_ADMISSION.md#supervisor-action-explanation).

## Automation transition admission

Reaction-planned child operations are projected into SCLite
`automation_chain.v0.1` artifacts. RExecOp owns runtime projection and
child-operation plan mechanics; SCLite owns the chain artifact shape; GovEngine
owns automation-transition admission through:

- `AutomationTransitionRequest`
- `admit_automation_transition()`
- `automation_transition_request_digest()`
- `automation_transition_admission_digest()`
- `explain_automation_transition()`

RExecOp records the request digest, admission digest and redacted explanation in
the reaction admission binding and writes the GovEngine admission digest onto the
`admitted_child` edge. The runtime still reports the binding as `unavailable`
for older local GovEngine installs. The supported package line is defined by
the exact `govengine` pin in `pyproject.toml`.

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

## Governance controls (operator projection)

RExecOp exposes a **read-only** operator catalog for GovEngine typed-execution
controls. This closes the gap between `doctor` (runtime readiness) and
`govengine-policy compatibility` (machine report) with a single CLI aimed at
operators reviewing controls before planning work.

```bash
rexecop governance controls
rexecop governance controls --profile examples/profiles/runtime-fixture/profile.yaml --track readonly
```

Output schema: `rexecop.governance_controls.v0.1`.

| Field | Source | Authority |
| --- | --- | --- |
| `control_catalog`, `typed_execution_stack` | GovEngine `typed_execution_control_catalog()` + stack compatibility | GovEngine owns control definitions; RExecOp projects |
| `profile_governance` (optional) | GovEngine `explain_profile_governance()` via `evaluate_profile_governance()` | Profile-owned declarations; GovEngine explains |
| `non_claims` | RExecOp CLI contract | No admission, no mutation |

**When to use what:**

| Need | Command |
| --- | --- |
| Runtime root, packages, stack contracts | `rexecop doctor` |
| Typed-execution control list + optional profile governance | `rexecop governance controls` |
| Policy reasoning for one operation-shaped request | `rexecop policy explain` |
| Policy impact on action shape without planning | `rexecop action policy-preview` |
| Machine-readable supported-contract report | `govengine-policy compatibility --json` |

`governance controls` does **not** replace GovEngine policy simulation or
admission. It does not add a second PolicyEngine to RExecOp.

## Boundary

GovEngine validates/contracts admission and runner records. RExecOp remains the runner,
orchestrator, and executor that invokes profile-declared workflow steps and connectors.
