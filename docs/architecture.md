# Architecture

RExecOp implements **Regulated Execution Operations**: profile-defined workflows executed under
GovEngine admission with auditable outcomes projected into SCLite.

## Layer boundaries

```text
Profiles (tecrax, examples/runtime-fixture)
  intents, workflows, connector contracts, validation_rules/

RExecOp (this package)
  runtime root, operation lifecycle, OperationPlan, orchestration,
  connector dispatch, validation engine, escalation packaging

GovEngine
  admission, governance meaning, runner request/receipt contracts

SCLite
  auditable artifacts, scoped tickets, receipts, review bundles
```

| Layer | Owns | Does not own |
| --- | --- | --- |
| **Profiles** | Domain semantics, success criteria YAML | Execution mechanics, policy meaning |
| **RExecOp** | Runtime root, state machine, step order, retry/pause, locks/queue | Policy decisions, artifact schemas |
| **GovEngine** | Allowed/blocked/approval_required | Connector calls, workflow invention |
| **SCLite** | Truth records and review semantics | Operation scheduling, infra APIs |

## Normal execution path

```text
profile-defined intent
  -> profile workflow (declared steps only)
  -> RExecOp OperationPlan (runtime artifact, not SCLite truth)
  -> [mutating modes] GovEngine admission / decision
  -> RExecOp controlled execution (connectors, internal actions)
  -> internal evidence events + shared workflow state
  -> declarative profile validation
  -> RExecOp emits SCLite artifact bundle (lifecycle + admission -> artifact shapes)
  -> SCLite validates bundle / review semantics
  -> completion | failure | escalation
```

GovEngine participates **before mutating execution**, not as a separate post-runner stage.
RExecOp remains the sole executor; it **projects** execution outcomes and bridged admission
metadata into SCLite fields (`policy_decision`, tickets, receipts).

## Core invariants

1. **RExecOp** decides operational mechanics (state, next step, retry, pause, queue).
2. **GovEngine** decides governance meaning (allowed, blocked, approval required).
3. **SCLite** records auditable truth.
4. **Profiles** own domain semantics — zero domain imports in `src/rexecop`.
5. **Domain plugins** register via `rexecop.internal_actions` and `rexecop.connector_backends` entry points (e.g. `tecrax`).

RExecOp must not become a second policy engine. Receipt exports under `.rexecop/receipts/` are
operator summaries; authoritative bundles live under `.rexecop/sclite/<operation_id>/`.

## Plugin boundaries

```text
src/rexecop/                         tecrax (or other domain packages)
  internal_registry.py                 rexecop.internal_actions entry point
  connector backends                   optional profile-owned plugin entry points
  StaticFixtureRuntime (generic)       Tecrax internal actions via entry point
  record_rollback_marker (builtin)     profile-owned normalizers/aggregators
```

The bundled `first-run-demo` and `runtime-fixture` profiles use the generic
`static_fixture` backend. Tecrax and other domain packages may register internal
actions, but RExecOp core does not import or own their semantics.

## Storage boundary

```text
OperationStoragePort (protocol)
  ├── FileStore (default)     local JSON under selected runtime root
  ├── SqliteStore (optional)  operations/plans/evidence in rexecop.db; aux dirs on disk
  └── InMemoryStore (tests)   operations/plans/evidence in RAM; SCLite dir still on disk
```

The selected runtime root is RExecOp operator storage, not parallel SCLite truth authority.
It is chosen by global `--root`, `REXECOP_ROOT`, named `--instance` /
`REXECOP_INSTANCE`, or fallback `./.rexecop`.
See [storage-backends.md](../docs/storage-backends.md) for File vs SQLite boundaries.

## Package map (current)

```text
src/rexecop/
  operation/          model, plan, state machine, controller
  orchestration/      workflow execution coordinator
  workflow/           YAML loader, step runner
  execution/          step executor, ExecutionRequest/Receipt model, bounded output helpers
  connectors/         mock, http_api, local_shell, ssh_readonly, composite runtime, fixture loader
  adapters/
    govengine_port/   admission client + static test adapter
    sclite_port/      artifact emitter, full bundle, fixture bundle (lab), placeholder (deprecated)
  profile/            contract loader, resolver, validation_rules
  environment/        environment loader, targets, policy criticality
  policy/             GovEngine PolicyEngine pack compile + connector/operation gates
  secrets/            secret_ref resolver port
  evidence/           internal events, redaction
  storage/            file store, sqlite store, factory, storage port protocol
  runtime_ops/        queue, target lock, maintenance, rollback, coordinator
  validation/         declarative rule evaluator
  escalation/         failure package assembly
  cli.py              operator commands
```

## GovEngine relationship

GovEngine composes and validates `RuntimeAdmissionResult` and runner request/receipt shapes.
RExecOp calls the GovEngine adapter before mutating execution and maps admission metadata into
SCLite lifecycle artifacts. GovEngine `0.16.1` PolicyEngine evaluates
`environment.policy_pack` at plan and on every
connector invoke. At operation level, RExecOp consumes a digest-bound GovEngine
`PolicyEnforcementPlan` plus its existing `GovAdmissionDecision` and mechanically
enforces the supported neutral controls.
Unsupported controls and drift fail closed before backend IO. Connector-level policy
remains plain-allow-only. GovEngine does **not** execute operations or invoke connectors.

Workflow execution additionally records `ExecutionRequest` / `ExecutionReceipt` in operation
`shared_state` — see [execution-contract.md](execution-contract.md).

## SCLite relationship

RExecOp emits a full GovEngine-integration lifecycle bundle on the completion path (scoped
ticket v0.3, trust/carrier sidecars, kernel guard manifest, `review_bundle` pass). Internal
evidence events under `.rexecop/evidence/` are runtime telemetry, not long-term truth.

## Storage layout

| Path | Role |
| --- | --- |
| `<root>/operations/` | Operation envelope + OperationPlan JSON (`file` backend) |
| `<root>/rexecop.db` | Operations, plans, evidence (`sqlite` backend) |
| `<root>/evidence/` | Redacted internal lifecycle events |
| `<root>/sclite/<op>/` | Authoritative SCLite artifact bundle |
| `<root>/receipts/` | Non-authoritative export summary |
| `<root>/approvals/` | Manual approval stub files |
| `<root>/queue/`, `locks/`, `inbox/` | Queue drain, target lock, file-drop triggers |

All paths are gitignored. Queue, locks, SCLite bundles, and receipts stay on disk for both
`file` and `sqlite` storage backends.
