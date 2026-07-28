# Operation lifecycle

RExecOp operation states and runtime controls for profile-defined workflows.

## States

| State | Meaning |
| --- | --- |
| `created` | Operation record allocated |
| `planned` | Plan generated; read-only may auto-approve on start |
| `waiting_for_approval` | GovEngine requires manual approval |
| `approved` | Ready to start (or queued for runtime capacity) |
| `running` | Executing workflow steps |
| `paused` | Stopped at a `pause_safe` step |
| `resuming` | Transition back to `running` |
| `retrying` | Operator or policy-driven retry in progress |
| `validating` | Declarative profile validation |
| `completed` / `failed` / `cancelled` / `escalated` / `blocked` | Terminal outcomes |

Invalid transitions raise typed `RExecOpStateError`. Every transition emits evidence.

## Supported modes

| Mode | Typical use |
| --- | --- |
| `dry_run` | Default-safe planning and read-only execution |
| `observe` | Read-only observation |
| `emergency_readonly` | Constrained read-only |
| `apply` | Mutating mechanics — blocked by default stable posture; `lab_only` still requires GovEngine + approval gates |
| `recovery` | Mutating recovery mechanics — same posture and governance gates |

## CLI orchestration

Lifecycle-related commands: `plan`, `approve`, `start`, `pause`, `resume`, `cancel`,
`retry`, `rollback`, `validate`, `escalate`, `status`, `history`, `queue`, `worker run`
and `trigger`.

The full grouped CLI reference (including pre-run inspection, triage, profile developer
surface and reaction commands) lives in [cli-reference.md](cli-reference.md).

`REXECOP_MUTATION_POSTURE` defaults to `stable_read_only`. Planning and approval may
still produce an `approved` operation for review, but start/advance/resume/retry and
connector dispatch reject mutating modes with `mutation_not_certified`. The explicit
`lab_only` value exists for bounded development tests and is not stable certification.

See [runtime-recovery-ops.md](runtime-recovery-ops.md) for triage, recovery and backup
workflows. See [profile-developer-surface.md](profile-developer-surface.md) and
[secrets-operator.md](secrets-operator.md) for developer and secret-resolution surfaces.

Before a persisted runtime store is opened, RExecOp reads the root manifest and
fails closed on missing or malformed data, unsupported schema or runtime major,
alpha-to-v1 reuse, and configured backend mismatch. Only explicit `rexecop init`
may create a missing manifest, and then only for an absent root or a strictly
empty real directory reached without symbolic-link components. Manifest reads
are bounded to 64 KiB, require a no-follow regular file and reject duplicate
JSON keys. `doctor` evaluates this same decision read-only; backup restore
validates the archived manifest while staging a deliberate new root. There is
no in-place migration, downgrade or backend conversion contract, and this guard
does not make older binaries safe against newer roots.

## Runtime policy

Configured per environment under `safety`:

| Key | Default | Effect |
| --- | --- | --- |
| `max_concurrent_operations` | `1` | Limits active running/paused/validating ops |
| `target_lock_enabled` | `true` | One mutating apply per `(environment, target)` |
| `maintenance_windows` | `[]` | When set, apply blocked outside declared windows |
| `apply_requires_govengine` | `true` | Documented operator expectation |
| `secrets_source` | `external` | Secrets via `secret_ref` / env / secrets file |

Queued operations stay in `approved` with `metadata.queue.status = pending` until the runtime
coordinator admits them after a slot frees.

Direct start, an `approved` `advance`, and FIFO drain consume pending work only
through the controller's private claim-specific path. Public coordinator
admission does not consume or carry a claim: when compatible bare pending state
exists, it returns `queued` without changing queue bytes, operation metadata or
target locks. Fenced or invalid queue state fails before those mutations.

Queue claims carry the complete current worker-lease identity. If a worker
dies immediately after claiming but before any lifecycle transition or attempt
record, a later strictly newer lease may requeue the still-`approved` operation
exactly once. Recovery does not replay active, transitioned, attempted,
indeterminate, missing, malformed, or inconsistent work. Those cases either
follow startup interruption/attempt recovery to terminal claim completion or
stop with the bounded `queue_claim_recovery_blocked` error before connector IO.

The exact claim tuple is operation id, owner token, process instance id, lease
epoch and queue attempt. Capacity or target-lock admission deferral verifies
that tuple under the worker-lease and queue locks, records one `requeued`
transition and preserves one pending entry. A repeated disposition for the
same tuple is a no-op; a delayed defer or completion from an earlier queue
attempt is fenced. If side-effect-free assessment admits an operation but the
final target-lock acquisition returns false, the controller exact-defers that
claim as `target_locked`. Exceptions raised by final acquisition or subsequent
persistence propagate without being relabelled or deferred. Operation queue
metadata is projected only after the queue transaction, so this is not a
cross-file ACID or backend exactly-once claim.

After a direct start or approved `advance` produces a durable terminal operation
and attempt result, the controller releases only that operation's target lock,
ensures the terminal receipt, exact-completes the selected claim, and only then
drains trailing work. A hard receipt failure leaves the exact claim `claimed`
and suppresses the drain. Existing strictly newer-lease recovery can close an
expired terminal claim; repeated terminal start then repairs the receipt without
connector replay. Terminal cleanup bypasses execution posture, maintenance,
catalog and rollback-execution checks and performs no connector IO.

An approved `advance` that durably completes only part of the workflow
exact-completes its admission claim after that progress, retains its target lock,
and produces neither a terminal receipt nor a trailing drain. A later `running`
`advance` continues without creating another queue claim.

Cancellation cleanup begins only after the lifecycle has validly and durably
entered `cancelled`. The controller then removes compatible queue state through
the private lease-fenced path, releases the target and drains trailing work.
Repeating cleanup repairs interruption after the state transition or after queue
removal. An unfinished `pending` or `started` attempt blocks before queue change,
target release or drain. `cancel` accepts exactly `waiting_for_approval`,
`approved`, `running` and `paused`; other source states remain rejected.
Cancellation does not interrupt connector I/O already synchronously in progress.

Public queue mutators, including public runtime release, retain compatibility
for empty, bare and completed state. Release validates and removes compatible
queue state before releasing the target. A `claimed` or `requeued` fence, or an
invalid topology, fails without changing queue bytes or releasing the target.

The private transition records bounded process identities, not owner-token
fields. If either bounded prior or current process identity is exactly equal to
either raw prior or current owner token, it is replaced by a deterministic,
bounded marker unequal to both tokens. Non-equal process identities remain
exact. This is an exact-value redaction rule; it does not claim protection from
arbitrary private-runtime-root manipulation or unrelated substring similarity.

Queue lifecycle operations use one private capability bound to the exact
built-in `FileStore`, `InMemoryStore` or `SqliteStore` instance. Custom storage
adapters and subclasses do not inherit that claim: they fail with the stable
`queue_claim_lifecycle_unsupported` error before queue mutation, public
queue-port calls or filesystem queue creation. Supporting such a store requires
an explicit implementation decision; RExecOp does not mix a logical adapter
claim with a filesystem disposition fallback.

## Rollback operations

`rollback` does not run the failed operation's rollback block in place. It creates a separate,
persisted operation and plan with a deterministic `<parent-id>-rollback` id, exact rollback mode
and steps, and a digest-bound link to the parent's failed outcome. The child and reciprocal parent
link are stored before execution, so replay after a crash recovers the same child instead of
creating or invoking a second rollback.

The rollback child receives a fresh plan-level GovEngine decision. Connector steps also require
fresh signed per-attempt authority and use the ordinary execution lease, permit, pre-I/O
revalidation, attempt journal and receipt-conformance path. Parent admission, a parent boolean
allow result, and parent manual approval are never rollback authority. If GovEngine returns an
approval-required decision, the child remains `waiting_for_approval`; approve and then start the
child operation id returned by `rollback`. Re-running `rollback` only reports that same child.

The current rollback workflow block declares no input projection, so the child starts with an
empty `shared_state`. Parent connector/mutation/internal results, continued failures, executed
steps, step results, receipts, typed specs/admissions and execution controls are not inherited.
Rollback steps may produce their own namespaced results only after their own execution.

For a catalog-bound parent, the child copies the persisted `catalog_runtime` reference and exact
parent-plan `catalog_binding`; rollback preparation does not re-resolve the catalog. The ordinary
controller `start`, `advance`, `resume` and `retry` entrypoints run one side-effect-free rollback
authority preflight before maintenance, admission or queue mutation. Resume repeats it before its
first transition/evidence event, and execution repeats it before permit allocation and immediately
before rollback connector I/O. Catalog, profile or environment drift therefore stops the child
without a connector call.

If that existing rollback-authority preflight fails after FIFO has selected a
derived rollback child, the controller exact-completes the selected snapshot,
removes the completed claim and operation queue metadata, and re-raises the
original validation error. This is queue cleanup only: it performs no connector
IO and adds no rollback execution, automatic retry or indeterminate-outcome
resolution behavior.

Immediately before rollback start and connector I/O, RExecOp verifies that the parent is still
`failed` and that its failure/terminal receipt and parent plan still match the persisted failure
authority digest. A parent retry or drift invalidates the child before connector invocation.
Rollback connector execution fails closed when canonical signed attempt authority is unavailable.
An indeterminate rollback outcome is never retried automatically or by `retry`; reconcile it.

## Workflow execution records

During `start`, `WorkflowRunner` writes:

- `shared_state.execution_request` — planned steps, target, mode, resource limits (`v0.2`)
- `shared_state.execution_receipt` — per-step digest refs and success/failure summary

These are runtime contracts for operator review and downstream binding — distinct from the
SCLite `execution_receipt` artifact emitted on the completion export path.
See [execution-contract.md](execution-contract.md).

## Operation explain

`rexecop operation explain --operation <id>` reads the stored operation and
`OperationPlan` and emits schema `rexecop.operation_explain.v0.1`. The output is
redacted for operator review: it includes profile/environment/catalog digests,
GovEngine decision and policy-enforcement blockers, expected SCLite artifact
roles, planned step ids/actions, rollback/preflight/postflight availability,
mutating contract completeness, and safe next commands. It does not execute,
approve, re-evaluate policy reasoning, or expose connector configuration.

## Authority boundaries

- **GovEngine** decides whether mutating work is allowed.
- **RExecOp** decides when/how steps run, pause, retry, rollback, queue, and lock.
- **SCLite** records auditable artifacts on the completion export path.
- **Profiles** define workflow steps and validation rules — the runner never invents steps.

## Storage

`FileStore` is the default backend under the selected runtime root. Select the root with
global `--root`, `REXECOP_ROOT`, named `--instance`, `REXECOP_INSTANCE`, or fallback
`./.rexecop`. Set `REXECOP_STORAGE=sqlite` or pass `--storage sqlite` for
SQLite-backed operations, plans, and evidence (`<root>/rexecop.db`); its
single-host queue, lease and durable attempt-journal auxiliaries remain under
the selected runtime root and are inventoried through the same logical store
port.
`storage/port.py` defines `OperationStoragePort` and `RuntimeStore` for optional backends.
The public `RuntimeStore` protocol does not promise the private built-in queue
claim lifecycle described above.

Operation metadata persists `profile_root` and sanitized `environment_connectors` for runtime
connector routing (`CompositeConnectorRuntime`).

## Vertical slice references

| Intent | Mode | Workflow |
| --- | --- | --- |
| `inspect_fixture_state` | `dry_run` / read-only | connector read → receipt |
| `apply_fixture_change` | `apply` | checkpoint → fixture mutation → checkpoint → receipt (+ rollback marker) |

The fixture profile is for deterministic runner regression only. Product/domain workflows
belong to external profiles such as Tecrax.
