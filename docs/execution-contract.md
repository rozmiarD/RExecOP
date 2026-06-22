# Execution request / receipt contract

RExecOp separates **what was approved to run** from **what actually ran** using
bounded runtime records in workflow `shared_state`. These are operator/runtime
contracts — not SCLite truth artifacts (see [sclite-integration.md](sclite-integration.md)).

## Schemas (`v0.1`)

| Record | ID pattern | Purpose |
| --- | --- | --- |
| `ExecutionRequest` | `exec-request:<operation_id>` | Planned steps, target, mode, resource limits |
| `ExecutionReceipt` | `exec-receipt:<operation_id>` | Per-step outcomes with digest refs, no raw output |

Module: `rexecop.execution.model`.

## When records are created

`WorkflowRunner.run()` (called from the operation orchestration path):

1. Builds `ExecutionRequest` via `execution_request_from_workflow()` from the
   planned step list, target, mode, and `max_steps`.
2. Stores `shared_state["execution_request"]` before the step loop.
3. On each terminal path (step failure or full success), builds
   `ExecutionReceipt` via `execution_receipt_from_results()` and stores
   `shared_state["execution_receipt"]`.

`source` on the request is always `approved_workflow_plan`.

## ExecutionRequest fields

- `request_id`, `operation_id`, `target_ref`, `mode`
- `steps[]`: `step_id`, `step_type`, `action`, `connector`, public metadata only
- `resource_limits`: `timeout_seconds`, `max_steps`, `max_output_bytes` (default 65536)

Steps are derived from workflow plan entries — RExecOp does not invent steps
outside the profile workflow.

## ExecutionReceipt fields

- `success`, `executed_steps[]`, optional `error` / `error_class`
- `step_receipts[]`: per-step `success`, `error_class`, `output_digest_refs`,
  `output_truncated`

Receipts reference digests produced by connectors; they do **not** embed raw
stdout/stderr or HTTP bodies.

## Bounded connector output

`rexecop.execution.output.bounded_text()` caps stored text by UTF-8 bytes while
computing a full-payload `sha256:` digest.

Connectors that emit bounded output today:

| Backend | Config | Response fields |
| --- | --- | --- |
| `local_shell_readonly` | `max_output_bytes` (default 65536) | `stdout`, `stderr`, `output_digests`, `output_truncated`, `output_sizes` |
| `ssh_readonly` | same | same |
| `http_api` | `max_response_bytes` (default 65536) | JSON payload or fail-closed oversized response metadata |

Truncated text is clipped for storage; digests always cover the full captured output.

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
| SCLite | `execution_receipt.v0.2` bundle artifact | Auditable truth on completion export |
| GovEngine | runner request/receipt contracts | Governance — see [govengine-integration.md](govengine-integration.md) |

## GovEngine PolicyEngine (wired)

When `environment.policy_pack` is set:

1. **Plan** — `PolicyEngine.evaluate()` builds an operation-level `PolicyVerdict`, projected to `GovPolicyDecision` in `govengine_request_preview.policy_decision` for `GovEngineClient` / `compose_runtime_admission_result()`.
2. **Connector invoke** — `CompositeConnectorRuntime` evaluates each `ConnectorRequest` against the same compiled pack **before** the backend runs. Deny / approval-required verdicts return `error_class: policy_denied` without subprocess/HTTP/SSH I/O.

Module: `rexecop.policy` (`pack.py`, `operation.py`, `connector.py`, `criticality.py`).

Without `policy_pack`, connector allowlists and mode checks behave as before.

## GovEngine note

GovEngine `0.15.0` ships the PolicyEngine MVP (`govengine.policy`). RExecOp uses it when `policy_pack` is configured; otherwise governance flows through `RuntimeAdmissionResult` compose with host-supplied summaries only.

## Related

- [operation-lifecycle.md](operation-lifecycle.md) — when workflows run
- [evidence-model.md](evidence-model.md) — `shared_state` linkage
- [connector-contract.md](connector-contract.md) — bounded output on shell backends
