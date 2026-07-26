# Execution request / receipt contract

RExecOp separates **what was approved to run** from **what actually ran** using
bounded runtime records in workflow `shared_state`. These are operator/runtime
contracts — not canonical SCLite lifecycle/evidence artifacts (see
[sclite-integration.md](sclite-integration.md)).

## Schemas (`v0.2`)

| Record | ID pattern | Purpose |
| --- | --- | --- |
| `ExecutionRequest` | `exec-request:<operation_id>` | Planned steps, target, mode, resource limits |
| `ExecutionReceipt` | `exec-receipt:<operation_id>` | Per-step outcomes with digest refs, no raw output |

Module: `rexecop.execution.model`.

## When records are created

`WorkflowRunner.run()` (called from the operation orchestration path):

1. Builds `ExecutionRequest` via `execution_request_from_workflow()` from the
   planned step list, target, mode, admitted resource limits, and policy binding.
2. Stores `shared_state["execution_request"]` before the step loop.
3. On each terminal path (step failure or full success), builds
   `ExecutionReceipt` via `execution_receipt_from_results()` and stores
   `shared_state["execution_receipt"]`.

`source` on the request is always `approved_workflow_plan`.

## Typed execution projections

Connector steps may compile digest-bound runtime projections before backend IO
via `rexecop.execution.typed_spec`:

| Schema | Purpose |
| --- | --- |
| `rexecop.step_execution_spec.v0.1` | Per-step envelope with backend class and payload digest |
| `rexecop.command_execution_spec.v0.1` | Allowlisted argv projection for shell/SSH readonly backends |
| `rexecop.http_action_execution_spec.v0.1` | Canonical HTTP action shape digest before `http_api` IO |
| `rexecop.static_fixture_execution_spec.v0.1` | Fixture action digest for neutral test backends |

When an operation seeds `shared_state.execution_context` with `profile_root` and
`environment.connectors`, `StepExecutor` compiles the typed spec, stores
`shared_state.typed_execution_specs[step_id].digest` before connector invoke,
and fail-closes on schema major-version mismatch or digest drift. Each
`StepExecutionSpec` may embed `rexecop.backend_capability_descriptor.v0.1`
(identity class, egress boundary, secret-ref requirements, live-backend posture).
These records are runtime projections only, not canonical SCLite artifacts.

The independent runtime release gate is evaluated before attempt allocation and again
at the composite connector boundary. Its default `stable_read_only` value rejects
`apply` / `recovery` with `mutation_not_certified`, even if GovEngine admission is
positive. `lab_only` exists to exercise mutation mechanics and makes `doctor` fail
stable readiness.

## ExecutionRequest fields

- `request_id`, `operation_id`, `target_ref`, `mode`
- `steps[]`: `step_id`, `step_type`, `action`, `connector`, public metadata only
- `resource_limits`: `timeout_seconds`, `max_steps`, `max_output_bytes` (default `65536` only when
  omitted). `max_output_bytes` is an exact positive integer; booleans, floats, strings, zero,
  negative and non-finite values are rejected before execution-request construction and connector
  IO.
- `policy_binding`: GovEngine enforcement plan, existing admission, pack, and verdict IDs/digests

Steps are derived from workflow plan entries — RExecOp does not invent steps
outside the profile workflow.

## ExecutionReceipt fields

- `success`, `executed_steps[]`, optional `error` / `error_class`
- `request_digest`, `receipt_digest`, and the same immutable `policy_binding`
- `enforcement`: resource limits, receipt emission, and output-digest verification status
- `step_receipts[]`: per-step `success`, `error_class`, `output_digest_refs`,
  `output_truncated`, optional `execution_spec_digest` and
  `capability_descriptor_digest` when typed execution specs were bound
- `typed_execution_binding`: aggregate digest map (`rexecop.typed_execution_binding.v0.1`)
  for executed connector steps; binds policy/admission via existing
  `policy_binding` and per-step typed/output digests without embedding payloads
- `governance_bindings`: per-step `RuntimeReceiptBinding v1` plus GovEngine
  `ReceiptConformanceResult v1` for signed-decision attempts; binds decision,
  immutable runtime permit, attempt, lease/fencing, inventory and output
  postconditions without embedding the signed decision or authorization nonce
- HTTP step bindings carry normalized scheme, effective port, address class and
  origin-binding digest. Per-step receipts compare the planned and observed
  destination binding and include the GovEngine admission/request digests; raw
  connector hosts are never included in this projection.

Receipts reference bounded runtime output-record digests and connector stream
digests where available; they do **not** embed raw stdout/stderr or HTTP bodies.
For `http_api`, the step output may include an `action_contract_digest` produced by
profile action-shape validation. The execution receipt binds the resulting bounded step
record digest rather than adding an HTTP-specific receipt field.

## Bounded connector output

For shell/SSH subprocesses, `max_output_bytes` is the exact hard combined stdout/stderr producer
budget. Binary streams are drained incrementally, retained bytes never exceed that combined
budget, and a producer crossing it fails as `output_limit_exceeded`; it is not successful
truncation. Counts and SHA-256 digests cover bytes actually drained.

Connectors that emit bounded output today:

| Backend | Config | Response fields |
| --- | --- | --- |
| `local_shell_readonly` | `max_output_bytes` (default 65536) | `stdout`, `stderr`, `output_digests`, `output_truncated`, `output_sizes` |
| `ssh_readonly` | same | same |
| `http_api` | `max_response_bytes` (default 65536) | JSON payload or fail-closed oversized response metadata |

The producer budget is distinct from the bounded diagnostic metadata required to explain a
failure. Before generic record-size handling, every claimed connector overflow is strictly
validated and replaced by an allowlisted `rexecop.output_limit_evidence.v0.1` projection. It
contains stream counts/digests, truncation flags, the producer limit, and the suppressed record's
digest/size; it never contains raw stdout/stderr, response errors, remote commands, return codes,
arbitrary connector fields or state deltas.

The projection's `overflow_evidence_envelope` declares `max_bytes: 2048` and its actual
`evidence_bytes`, measured over the final sorted-key compact ASCII JSON step output. This fixed
diagnostic envelope may be larger than `max_output_bytes`; neither the step nor receipt claims
otherwise. Invalid overflow evidence fails closed to the generic `validation_failed` envelope.
If the claimed overflow cannot be safely canonicalized, that envelope omits record digest and
record-size fields rather than claiming a digest or byte count that was not produced.
For internal handlers and other output, the producer/payload bound covers returned output and the
handler's `shared_state` delta; an oversized or exceptional step rolls that delta back and retains
only the generic bounded diagnostic envelope.

## Diagnostic partial failures

A profile may set `metadata.continue_on_error: true` only on a connector step in a
`read_only` workflow. The runner then:

- emits the normal `step_failed` evidence event;
- retains the failed per-step receipt;
- stores only bounded `step_id`, redacted error and `error_class` under
  `shared_state.continued_failures`;
- continues to later normalization and receipt steps.

The flag is ignored in mutating operation modes and rejected for internal/evidence steps.
An overall execution receipt with `success: true` means the declared diagnostic workflow
reached completion; it does not mean every component was healthy. Consumers must inspect
`step_receipts` and the profile validation result.

## Relationship to other layers

```text
Workflow plan (profile)
  -> ExecutionRequest (shared_state)
  -> step execution + connector responses (may include bounded text)
  -> ExecutionReceipt (shared_state, digest refs only)
  -> SCLite execution_receipt artifact (separate schema, completion path)
```

| Layer | Record | Authority |
| --- | --- | --- |
| RExecOp runtime | `execution_request` / `execution_receipt` in `shared_state` | Operator debugging, GovEngine/receipt binding inputs |
| SCLite | `execution_receipt.v0.2` bundle artifact | Canonical receipt contract and verification input |
| GovEngine | Runner request/receipt contracts | Governance and conformance checks |

SCLite `execution_receipt` may include `rexecop_runtime_binding` with digest-only
refs to the runtime receipt (`request_digest`, `receipt_digest`, `policy_binding`,
`typed_execution_binding`, `governance_bindings`). RExecOp remains the runtime
producer and persistence owner; SCLite retains canonical contract and
verification authority.

## GovEngine PolicyEngine (wired)

When `environment.policy_pack` is set:

1. **Plan** — `PolicyEngine.evaluate()` builds an operation-level `PolicyVerdict`.
   GovEngine binds the compiled pack and verdict into `PolicyEnforcementPlan` and
   projects it into the existing `GovAdmissionDecision` contract.
   Plain `allow` and enforceable `allow_with_obligations` may proceed; unsupported
   controls, deny, approval-required, and invalid values fail closed.
2. **Start/advance** — RExecOp recompiles the stored pack and validates the entire
   plan, admission, and digests before constructing the runner. Drift and unsupported backend
   capabilities stop execution before connector IO.
3. **Runtime enforcement** — `max_steps` bounds the whole declared workflow;
   `timeout` is a per-connector-call upper bound; `output_limit` bounds untrusted producer/payload
   bytes, while the separately declared fixed diagnostic envelope bounds overflow metadata;
   receipt and output digest obligations are checked on terminal receipt creation.
4. **Connector invoke** — `CompositeConnectorRuntime` independently evaluates each
   `ConnectorRequest` before the backend. Connector-level verdicts remain
   plain-allow-only; connector-specific obligations fail closed.

Module: `rexecop.policy` (`pack.py`, `operation.py`, `enforcement.py`,
`connector.py`, `criticality.py`).

Without `policy_pack`, connector allowlists and mode checks behave as before.

## GovEngine note

The exact pinned GovEngine release provides the PolicyEngine enforcement-plan
and existing-admission binding contracts. RExecOp uses them when `policy_pack`
is configured. Without a pack, the legacy unbound read-only runtime path remains
available for compatibility and must not be described as policy-bound
execution.

## Related

- [operation-lifecycle.md](operation-lifecycle.md) — when workflows run
- [sclite-integration.md](sclite-integration.md) — evidence projection and authority
- [connector-contract.md](connector-contract.md) — bounded output on shell backends
