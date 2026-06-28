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
govengine>=0.16.2,<0.17
```

Pinned compatible with the SCLite alpha line used by RExecOp (`sclite-core>=1.0.6,<1.1`).

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

## Boundary

GovEngine validates/contracts admission and runner records. RExecOp remains the runner,
orchestrator, and executor that invokes profile-declared workflow steps and connectors.
