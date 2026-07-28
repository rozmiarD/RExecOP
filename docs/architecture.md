# Architecture

RExecOp is a domain-neutral execution kernel and runtime. It coordinates
profile-defined operations, enforces runtime safety controls, performs connector
I/O and produces evidence. It is not the policy authority, domain model or
canonical evidence verifier.

## Ownership

| Component | Owns | Does not own |
| --- | --- | --- |
| Domain profiles | Intent meaning, workflows, target taxonomy, domain validation and required capabilities | Runtime lifecycle, policy decisions, canonical evidence verification |
| GovEngine | Policy, governance, admission, approvals, obligations, constraints and attempt-bound governance authorization | Runtime permits, queueing, leases, retries, connector I/O or artifact persistence |
| RExecOp | Planning, operation lifecycle, attempts, queue claims, leases, fencing, runtime permits, retries, recovery, connector dispatch and orchestration contracts | Domain meaning, organization policy, secret custody or SCLite contract authority |
| SCLite | Canonical evidence contracts, canonicalization, integrity, tickets, receipts, review bundles and verification | Runtime scheduling, connector execution, governance or orchestration semantics |

RExecOp owns the following orchestration contract families because it creates
and interprets their runtime semantics:

- observation and finding envelopes;
- reaction plans and escalation proposals;
- trigger and watchdog decisions;
- automation chains.

Their current resources live under `rexecop.contracts`, are resolved by
`ORCHESTRATION_SCHEMA_RESOLVER`, and use the `rexecop.io/*@v0.1` owner
namespace. Historical embedded `schema_ref` values are compatibility
identifiers, not evidence that SCLite owns these contracts. SCLite verifies
their canonical bytes and descriptors without interpreting their orchestration
meaning.

## Execution path

```text
profile intent and target
  -> validate profile, environment, catalog and workflow
  -> create Operation and OperationPlan
  -> [mutating mode] obtain and persist GovEngine decision
  -> claim work and acquire the current execution lease
  -> issue and validate an attempt-bound runtime permit
  -> persist an attempt before connector I/O
  -> recheck posture, permit, fencing and runtime bindings
  -> dispatch a connector or internal action
  -> persist result or outcome_indeterminate
  -> run profile-declared validation
  -> project lifecycle and evidence into SCLite-compatible artifacts
  -> verify the resulting bundle
  -> complete, fail, escalate or enter recovery
```

Mutating modes require GovEngine governance and remain blocked by the default
`stable_read_only` posture even if governance allows them. A configured policy
pack can also evaluate read-only work. The explicit `legacy_read_only` path has
no signed per-attempt GovEngine decision and must not be represented as
governance authenticity.

External I/O is not deterministic. RExecOp's determinism claim applies to
orchestration decisions over equivalent recorded inputs and state.

## Core invariants

1. An attempt is durable before connector I/O starts.
2. A stale lease or fencing token cannot authorize the current attempt.
3. Mutation posture and permit bindings are rechecked immediately before
   mutating I/O.
4. The post-I/O, pre-durable-result crash window is represented explicitly as
   `outcome_indeterminate`; RExecOp does not claim exactly-once side effects.
5. Unknown contract versions, unsupported controls and detected binding drift
   fail closed.
6. Profiles provide domain semantics without domain imports in `src/rexecop`.
   The bundled `examples/first-run-demo` source mirror and its versioned,
   materializable package resource exercise this boundary without becoming
   product profiles. Materialization is static file copying only, not runtime
   initialization, governance, connector dispatch, or evidence emission.
7. Runtime events and receipt exports are not parallel SCLite truth formats.
8. Secret values are host-owned and prohibited from profile/environment
   configuration and public evidence projections.

## Runtime and plugin boundaries

```text
RExecOp core                         domain or host packages
  connector and action ports          rexecop connector/action entry points
  generic runtime fixture              profile-owned implementations
  profile resolver                     profile declarations and validation
  secret-resolution port               host-owned secret provider
  storage ports                        host-selected storage implementation
  signer/verifier ports                host trust configuration
```

Installing RExecOp does not make a plugin, connector, secret provider, signer,
verifier or storage adapter trustworthy for a particular environment.
Capabilities and plugin inventory are inputs to governance and runtime checks,
not self-authenticating safety claims.

## Storage boundary

```text
OperationStoragePort
  ├── FileStore      stable, single-host default
  ├── SqliteStore    alpha backend; auxiliary state remains on disk
  └── InMemoryStore  tests only
```

The runtime root is selected by `--root`, `REXECOP_ROOT`, `--instance`,
`REXECOP_INSTANCE`, or the `./.rexecop` fallback. It is operator-managed
runtime state and should be treated as sensitive.

| Path | Role |
| --- | --- |
| `<root>/operations/` | Operation envelopes and plans for `FileStore` |
| `<root>/rexecop.db` | Operations, plans and evidence for `SqliteStore` |
| `<root>/evidence/` | Bounded internal runtime events |
| `<root>/sclite/<operation_id>/` | Emitted SCLite-compatible artifact bundle |
| `<root>/receipts/` | Non-authoritative operator receipt exports |
| `<root>/approvals/` | Manual approval stubs |
| `<root>/queue/`, `locks/`, `inbox/` | Queue, lock and host-trigger mechanics |

See [Storage backends](storage-backends.md) and
[SCLite integration](sclite-integration.md).

## Package map

```text
src/rexecop/
  action/            action metadata and projections
  adapters/          GovEngine and SCLite integration ports
  catalog/           target and operation catalog mechanics
  cli_groups/        CLI command implementations
  connectors/        connector implementations and dispatch
  contracts/         RExecOp-owned orchestration contracts and schemas
  environment/       environment and target validation
  escalation/        bounded failure packages
  evidence/          internal events and redaction
  execution/         execution specs, requests, receipts and output bounds
  governance/        runtime-side governance binding helpers
  observability/     structured logs and diagnostics
  operation/         model, plan, state machine and controller
  orchestration/     workflow execution coordination
  plugins/           plugin discovery and inventory
  policy/            GovEngine policy-pack integration
  profile/           profile loading, resolution and validation
  reaction/          deterministic reaction mechanics
  runtime/           root, posture, compatibility and readiness
  runtime_ops/       queue, lease, worker, watchdog and recovery mechanics
  secrets/           host secret-resolution port
  storage/           storage protocol and implementations
  triggers/          host-trigger planning mechanics
  validation/        declarative validation evaluator
  workflow/          workflow loading and step execution
```

## Exact stack baseline

RExecOp `1.0.0rc1` pins:

```text
govengine==1.0.0rc1
sclite-core==2.0.0
```

The exact contract and downstream-consumer baseline is maintained in
[Stack contract compatibility](stack-contract-compatibility.md).
