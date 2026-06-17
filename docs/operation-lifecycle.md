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
| `apply` | Mutating execution — GovEngine + approval gates |
| `recovery` | Mutating recovery path — same governance gates |

## CLI orchestration

| Command | Purpose |
| --- | --- |
| `plan` | Create operation + `OperationPlan`; GovEngine gate for mutating modes |
| `approve` | Manual approval after `approval_required` |
| `start` | Execute workflow (may queue if lock/capacity busy) |
| `pause` / `resume` | Pause only at workflow `pause_safe` steps |
| `cancel` | Abort from approved/running/paused/waiting states |
| `retry` | Operator retry after `failed` when profile retry policy allows |
| `rollback` | Execute explicit `workflow.rollback.steps` on failed operation |
| `validate` | Re-run declarative profile validation rules |
| `escalate` | Package failure for operator handoff |
| `queue` | Inspect FIFO run-now backlog |
| `status` / `history` | Operation state and evidence history |

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

## Authority boundaries

- **GovEngine** decides whether mutating work is allowed.
- **RExecOp** decides when/how steps run, pause, retry, queue, and lock.
- **SCLite** records auditable artifacts on the completion export path.
- **Profiles** define workflow steps and validation rules — the runner never invents steps.

## Storage

`FileStore` is the default backend under `.rexecop/`. `storage/port.py` defines
`OperationStoragePort` for future SQLite or remote backends.

Operation metadata persists `profile_root` and sanitized `environment_connectors` for runtime
connector routing (`CompositeConnectorRuntime`).

## Vertical slice references

| Intent | Mode | Workflow |
| --- | --- | --- |
| `check_backup_status` | `dry_run` / read-only | resolve → proxmox → pbs → correlate → receipt |
| `restart_zabbix_agent` | `apply` | capture → restart → verify → receipt (+ optional rollback) |

Fixtures: `examples/profiles/tecrax-fixture/` and external `tecrax`.
