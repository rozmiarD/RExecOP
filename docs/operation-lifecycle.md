# Operation lifecycle

RExecOp operation states and runtime controls for profile-defined workflows.

## States

| State | Meaning |
| --- | --- |
| `created` | Operation record allocated |
| `planned` | Plan generated; read-only may auto-approve next |
| `waiting_for_approval` | GovEngine requires manual approval |
| `approved` | Ready to start (or queued for runtime capacity) |
| `running` | Executing workflow steps |
| `paused` | Stopped at a `pause_safe` step |
| `resuming` | Transition back to `running` |
| `retrying` | Automatic or manual retry in progress |
| `validating` | Deterministic profile validation |
| `completed` / `failed` / `cancelled` / `escalated` / `blocked` | Terminal outcomes |

## Orchestration commands

| Command | Purpose |
| --- | --- |
| `plan` | Create operation + GovEngine gate for mutating modes |
| `approve` | Manual approval after `approval_required` |
| `start` | Execute workflow (may queue if lock/capacity busy) |
| `pause` / `resume` | Pause only at `pause_safe` steps |
| `cancel` | Abort before completion |
| `retry` | Operator retry after `failed` when policy allows |
| `rollback` | Execute explicit workflow `rollback.steps` on failure |
| `validate` | Re-run deterministic validation |
| `escalate` | Package failure for operator handoff |
| `queue` | Inspect FIFO run-now backlog |

## Runtime policy (Phase 6)

Configured per environment under `safety`:

| Key | Default | Effect |
| --- | --- | --- |
| `max_concurrent_operations` | `1` | Limits active running/paused/validating ops |
| `target_lock_enabled` | `true` | One mutating apply per `(environment, target)` |
| `maintenance_windows` | `[]` | When set, apply blocked outside declared windows |

Queued operations stay in `approved` with `metadata.queue.status = pending` until `process_queue` admits them after a slot frees.

## Authority boundaries

- **GovEngine** decides whether mutating work is allowed.
- **RExecOp** decides when/how steps run, pause, retry, queue, and lock.
- **SCLite** records auditable artifacts on receipt export.

## Storage

`FileStore` is the default backend. `storage/port.py` defines `OperationStoragePort` for future SQLite or remote backends behind an explicit feature flag.
