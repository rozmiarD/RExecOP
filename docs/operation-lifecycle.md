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
- **RExecOp** decides when/how steps run, pause, retry, queue, and lock.
- **SCLite** records auditable artifacts on the completion export path.
- **Profiles** define workflow steps and validation rules — the runner never invents steps.

## Storage

`FileStore` is the default backend under the selected runtime root. Select the root with
global `--root`, `REXECOP_ROOT`, named `--instance`, `REXECOP_INSTANCE`, or fallback
`./.rexecop`. Set `REXECOP_STORAGE=sqlite` or pass `--storage sqlite` for
SQLite-backed operations, plans, and evidence (`<root>/rexecop.db`).
`storage/port.py` defines `OperationStoragePort` and `RuntimeStore` for optional backends.

Operation metadata persists `profile_root` and sanitized `environment_connectors` for runtime
connector routing (`CompositeConnectorRuntime`).

## Vertical slice references

| Intent | Mode | Workflow |
| --- | --- | --- |
| `inspect_fixture_state` | `dry_run` / read-only | connector read → receipt |
| `apply_fixture_change` | `apply` | checkpoint → fixture mutation → checkpoint → receipt (+ rollback marker) |

The fixture profile is for deterministic runner regression only. Product/domain workflows
belong to external profiles such as Tecrax.
