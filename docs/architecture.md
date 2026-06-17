# Architecture

RExecOp implements **Regulated Execution Operations**: profile-defined workflows executed under
GovEngine admission with auditable outcomes projected into SCLite.

## Layer boundaries

```text
Profiles (tecrax, examples/tecrax-fixture)
  intents, workflows, connector contracts, validation_rules/

RExecOp (this package)
  operation lifecycle, OperationPlan, orchestration,
  connector dispatch, validation engine, escalation packaging

GovEngine
  admission, governance meaning, runner request/receipt contracts

SCLite
  auditable artifacts, scoped tickets, receipts, review bundles
```

| Layer | Owns | Does not own |
| --- | --- | --- |
| **Profiles** | Domain semantics, success criteria YAML | Execution mechanics, policy meaning |
| **RExecOp** | State machine, step order, retry/pause, locks/queue | Policy decisions, artifact schemas |
| **GovEngine** | Allowed/blocked/approval_required | Connector calls, workflow invention |
| **SCLite** | Truth records and review semantics | Operation scheduling, infra APIs |

## Normal execution path

```text
profile-defined intent
  -> profile workflow (declared steps only)
  -> RExecOp OperationPlan (runtime artifact, not SCLite truth)
  -> GovEngine admission / decision (mutating modes)
  -> RExecOp controlled execution
  -> connector runtime action (mock | http_api | local_shell_readonly)
  -> internal evidence events + shared workflow state
  -> declarative profile validation
  -> SCLite artifact bundle emission
  -> completion | failure | escalation
```

## Core invariants

1. **RExecOp** decides operational mechanics (state, next step, retry, pause, queue).
2. **GovEngine** decides governance meaning (allowed, blocked, approval required).
3. **SCLite** records auditable truth.
4. **Profiles** own domain semantics — zero domain imports in `src/rexecop`.
5. **Domain plugins** register via `rexecop.internal_actions` and `rexecop.connector_backends` entry points (e.g. `tecrax`).

RExecOp must not become a second policy engine. Receipt exports under `.rexecop/receipts/` are
operator summaries; authoritative bundles live under `.rexecop/sclite/<operation_id>/`.

## Plugin boundaries (Phase 11)

```text
src/rexecop/                         tecrax (or other domain packages)
  internal_registry.py                 rexecop.internal_actions entry point
  fixture_loader.py                    rexecop.connector_backends entry point
  MockConnectorRuntime (generic)       TecraxFixtureConnectorRuntime (offline mock)
  record_rollback_marker (builtin)     correlate_vm_backup_coverage, ...
```

Mock connectors without `fixture:` in environment YAML use the **generic** mock (unsupported
actions fail). Tecrax offline workflows set `fixture: tecrax_fixture` on mock connectors.

## Storage boundary

```text
OperationStoragePort (protocol)
  ├── FileStore (default)     local JSON under .rexecop/ — single-operator default
  └── InMemoryStore (tests)   operations/plans/evidence in RAM; SCLite dir still on disk
```

`.rexecop/` is RExecOp runtime operator storage, not parallel SCLite truth authority.

## Package map (current)

```text
src/rexecop/
  operation/          model, plan, state machine, controller
  orchestration/      workflow execution coordinator
  workflow/           YAML loader, step runner
  execution/          step executor, internal action plugin registry
  connectors/         generic mock, http_api, local_shell, composite runtime, fixture loader
  adapters/
    govengine_port/   admission client + static test adapter
    sclite_port/      artifact emitter, full bundle, placeholder (deprecated)
  profile/            contract loader, resolver, validation_rules
  environment/        environment loader, connector config sanitization
  secrets/            secret_ref resolver port
  evidence/           internal events, redaction
  storage/            file store + storage port protocol
  runtime_ops/        queue, target lock, maintenance, rollback, coordinator
  validation/         declarative rule evaluator
  escalation/         failure package assembly
  cli.py              operator commands
```

## GovEngine relationship

GovEngine composes and validates `RuntimeAdmissionResult` and runner request/receipt shapes.
RExecOp calls the GovEngine adapter before mutating execution and maps admission metadata into
SCLite `policy_decision` fields. GovEngine does **not** execute operations or invoke connectors.

## SCLite relationship

RExecOp emits a full GovEngine-integration lifecycle bundle on the completion path (scoped
ticket v0.3, trust/carrier sidecars, kernel guard manifest, `review_bundle` pass). Internal
evidence events under `.rexecop/evidence/` are runtime telemetry, not long-term truth.

## Storage layout

| Path | Role |
| --- | --- |
| `.rexecop/operations/` | Operation envelope + OperationPlan JSON |
| `.rexecop/evidence/` | Redacted internal lifecycle events |
| `.rexecop/sclite/<op>/` | Authoritative SCLite artifact bundle |
| `.rexecop/receipts/` | Non-authoritative export summary |
| `.rexecop/approvals/` | Manual approval stub files |

All paths are gitignored.
