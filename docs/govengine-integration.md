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

## Phase 2B (current)

- Real adapter: `GovEngineClient` via `compose_runtime_admission_result()`
- Default adapter: `GovEngineClient` (fail-closed without full admission inputs)
- Bootstrap/tests: `StaticGovEngineAdapter` via `static_govengine_adapter()`
- Dependency: `govengine>=0.12.2a0,<0.15`
- Runner shape helper: `build_runner_request_preview()` for Phase 4+

## Decision types

Mapped from `RuntimeAdmissionResult` to RExecOp `GovEngineDecisionType`.

## Apply hard rule

Mutating modes (`apply`, `recovery`) require `allowed` admission before RExecOp may
permit mutating workflow steps.

## Boundary

GovEngine validates/contracts admission and runner records. RExecOp remains the
runner, orchestrator, and executor.
