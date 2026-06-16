# GovEngine integration

RExecOp consumes GovEngine for **governance decisions only**. GovEngine defines and
validates runner request/receipt contracts; it does **not** execute operations.

```text
Profile workflow
  -> RExecOp OperationPlan
  -> GovEngineRequest (from plan preview)
  -> GovEngineAdapter.evaluate()
  -> GovEngineDecision
  -> RExecOp state transition + evidence
  -> (Phase 4+) controlled execution when allowed
```

## Phase 2A (current)

- Port: `src/rexecop/adapters/govengine_port/`
- `StaticGovEngineAdapter` — bootstrap/test only, **not production governance**
- No `govengine` PyPI dependency yet (Phase 2B)
- Apply/recovery modes call the adapter before mutating execution is permitted

## Decision types

`allowed`, `blocked`, `approval_required`, `maintenance_window_required`,
`backup_required`, `read_only_only`, `human_required`, `unsupported`, `error`

## Apply hard rule

Mutating modes (`apply`, `recovery`) require a positive `allowed` decision before
RExecOp may permit mutating workflow steps. Other decisions route to
`waiting_for_approval` or `blocked` states.

## Phase 2B (future)

Real adapter maps to `govengine` admission surfaces, `GovRunnerRequest` /
`GovRunnerReceipt`, and `validate_runner_receipt_binding()`.
