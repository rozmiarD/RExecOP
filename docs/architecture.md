# Architecture

RExecOp sits between domain profiles and the governance/truth stack.

```text
Profiles (Tecrax, Ravenclaw, …)
  intents, workflows, connectors, validation rules

RExecOp
  operation lifecycle, planning, execution mechanics,
  pause/resume/retry, connector dispatch, escalation packaging

GovEngine
  governance decisions, admission, runner request/receipt contracts

SCLite
  auditable artifacts, receipts, evidence bundles
```

## Normal execution path (target)

```text
profile-defined intent
  -> profile workflow
  -> RExecOp OperationPlan (runtime artifact)
  -> GovEngine governance request / decision
  -> RExecOp controlled execution
  -> connector/runtime action
  -> evidence collection
  -> deterministic validation
  -> SCLite-compatible receipt
  -> completion / failure / escalation
```

## Invariants

1. **RExecOp** decides operational mechanics (state, next step, retry, pause).
2. **GovEngine** decides governance meaning (allowed, blocked, approval required).
3. **SCLite** records auditable truth.
4. **Profiles** own domain semantics.

RExecOp must not become a second policy engine. RExecOp must not duplicate SCLite as a long-term source of truth.

## Phase 0 scope

Phase 0 provides package skeleton, CLI (`version`, `--help`), docs, CI, and smoke tests only.

Future modules (not implemented in Phase 0):

- `operation/` — state machine, OperationPlan, controller
- `adapters/govengine_port/` — governance port (2A static, 2B real)
- `adapters/sclite_port/` — evidence emission (3A placeholder, 3B real)
- `workflow/`, `execution/`, `connectors/`, `profile/`, `environment/`
- `evidence/`, `storage/`, `validation/`, `escalation/`

## GovEngine relationship

GovEngine defines and validates runner request/receipt contracts and admission decisions. RExecOp remains the component that runs workflows and invokes connectors after governance allows execution.

## SCLite relationship

Internal evidence events may exist for runtime debugging, but authoritative receipts and artifacts are emitted in SCLite-compatible form in later phases. Receipt exports under `.rexecop/` are summaries, not a parallel truth layer.
