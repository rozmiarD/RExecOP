# Host-owned scheduling

RExecOp does not ship a cron engine, calendar service or recurrence DSL. A host
scheduler such as systemd, cron or an external orchestrator may invoke the
RExecOp CLI. RExecOp owns queue, claim, worker, trigger and watchdog mechanics
after invocation; the host owns when invocation occurs.

Use an explicit runtime root through `REXECOP_ROOT` or `--root`. The fallback is
`./.rexecop`.

## One-shot read-only operation

```bash
OPERATION=$(
  rexecop --root /var/lib/rexecop plan \
    --catalog /etc/rexecop/catalog.yaml \
    --intent inspect \
    --target fixture-target \
    --mode dry_run
)

rexecop --root /var/lib/rexecop start --operation "$OPERATION"
```

The selected profile, environment and catalog determine whether the example is
actually valid. A host schedule does not bypass profile validation, governance
requirements, mutation posture or connector safety checks.

## Queue worker

Drain one eligible queued operation:

```bash
rexecop --root /var/lib/rexecop worker run --once
```

Run a polling worker:

```bash
rexecop --root /var/lib/rexecop worker run --poll-interval 30
```

Equivalent one-shot queue drain:

```bash
rexecop --root /var/lib/rexecop queue --drain
```

The worker starts only operations eligible under the persisted state and
current runtime controls. The default `stable_read_only` posture continues to
block mutating execution.

At startup the worker reconciles abandoned queue claims while holding the
worker-lease lock and then the queue lock for the complete transaction.
Only an expired prior-epoch claim for an exactly `approved` operation with zero
logical-store attempt records is automatically requeued. That queue repair does
not repeat connector execution. Every attempt-bearing, active,
indeterminate, missing, malformed, stale-lease or inconsistent case is kept
from dequeue and connector IO with `queue_claim_recovery_blocked`; active and
`started` work first follows the ordinary interruption recovery path. This does
not grant new GovEngine authority or provide distributed exactly-once delivery.

Enqueue, claim, admission defer, completion and recovery all resolve the same
private lifecycle bound to the exact built-in store. Where all three are
needed, the lock order is worker lease, then run-now queue, then read-only
logical operation and attempt facts.
Admission defer and expired-claim recovery therefore cannot claim in one
backend and disposition in another. Operation metadata and target-lock changes
remain outside that lock set; there is no cross-file ACID guarantee. Custom
storage adapters and built-in subclasses without explicit support stop with
`queue_claim_lifecycle_unsupported` before queue mutation or filesystem
fallback.

The controller owns claim-specific consumption for direct start, approved
`advance` and FIFO drain. Public admission leaves compatible bare pending state
queued and byte-identical; it does not claim or discard that entry. Once the
controller owns an exact snapshot, capacity or target contention exact-defers
that snapshot with one pending entry. Stale dispositions are fenced.

For a durable terminal result, the controller orders target-only release,
terminal receipt, exact claim completion and trailing drain. A hard receipt
failure leaves the claim fenced and suppresses drain; strictly newer-lease
recovery and a repeated terminal cleanup repair the receipt without connector
replay. A partial approved `advance` instead completes only its admission claim,
retains the target and performs no terminal receipt or drain.

After a valid durable `cancelled` transition, cleanup removes queue state under
the current lease, releases the target and drains. Repeating it repairs either
an interruption before queue cleanup or one before target release. This does not
claim that `cancel` accepts `approved` or `paused` as source states. A derived
rollback candidate whose existing authority preflight fails has its FIFO claim
and queue metadata cleaned before the original error is returned; that cleanup
does not execute, retry or automatically resolve rollback work.

Public queue mutation and public release validate compatible state before
target release. Fenced or invalid state leaves both queue bytes and target lock
unchanged. These mechanics remain single-host: they do not provide a
distributed queue, cross-file ACID, backend exactly-once execution, claim
renewal or power-loss durability.

## Trigger input

A wrapper may submit a bounded JSON request:

```bash
printf '%s\n' \
  '{"profile":"examples/profiles/runtime-fixture/profile.yaml","env":"examples/environments/runtime-fixture.policy.example.yaml","intent":"inspect_fixture_state","target":"fixture-target","mode":"dry_run","auto_start":true}' \
  | rexecop --root /var/lib/rexecop trigger
```

Or a worker can watch `<root>/inbox/*.json`:

```bash
rexecop --root /var/lib/rexecop worker run \
  --watch-inbox \
  --poll-interval 60
```

Without watchdog mode, an inbox item that fails processing is moved immediately
to `<root>/inbox/failed/inbox-<random>.json`, outside the direct inbox selection.
The worker emits a redacted `inbox_item_quarantined` structured runtime log and
continues its current snapshot. If either the bounded no-overwrite move or that
required log fails, the worker stops before later inbox or queue work. Operators
must inspect and replay quarantined input explicitly; RExecOp does not replay it
automatically. Producers remain responsible for atomically publishing complete,
immutable JSON files into the inbox.

A neutral trigger event can be mapped by profile-owned
`triggers/trigger_rules.yaml`:

```json
{
  "profile": "examples/profiles/runtime-fixture/profile.yaml",
  "env": "examples/environments/runtime-fixture.policy.example.yaml",
  "trigger_event": {
    "id": "evt-001",
    "source": "lab-wrapper",
    "type": "fixture.state_observed",
    "subject": "fixture-target",
    "occurred_at": "2026-06-28T12:00:00+00:00",
    "payload": {"status": "degraded"},
    "rule_set": "fixture.triggers"
  }
}
```

RExecOp owns the trigger-decision contract and planning mechanics. Profiles own
event-field meaning and operation mapping. Before `plan_operation` creates an
operation, RExecOp submits a bounded GovEngine planning-admission request.
Planning admission is not an execution permit. The normal pre-I/O governance
path still applies to any executable operation.

Trigger decisions are limited to `plan_operation`, `ignore`, `escalate`,
`drop_duplicate` and `cooldown_blocked`. A planning decision creates an
operation plan but does not start it unless the explicit trigger input requests
auto-start and all ordinary controls allow it.

## Watchdog

The worker can inspect its own runtime mechanics:

```bash
rexecop --root /var/lib/rexecop worker run \
  --watch-inbox \
  --watchdog \
  --stale-inbox-seconds 3600 \
  --stale-operation-seconds 3600 \
  --inbox-retry-budget 3 \
  --poll-interval 60
```

The watchdog is not infrastructure monitoring. It records bounded worker,
queue, inbox and stale-operation observations under `<root>/watchdog/`.
Exhausted inbox retries move files to `<root>/dead_letter/`. Stale operations
produce a `block_autostart` record; the watchdog does not rewrite the operation
state to hide the condition.

Watchdog retry attempts below the configured budget intentionally leave an item
in the inbox. At the budget, GovEngine supervisor admission still precedes the
same bounded no-overwrite move into `dead_letter`; retry state is cleared only
after that move and the required watchdog record/projection persistence succeed.
A failed final move keeps the reached attempt count capped at the configured
budget so a later poll can retry that governed move after the environment is
repaired. These same-filesystem moves do not claim directory fsync or power-loss
durability, and quarantine logs are runtime diagnostics, not canonical evidence
or governance decisions. Source and reservation identities are revalidated
immediately before the move; this does not claim protection against a
non-cooperating same-UID process that races a replacement after that final
check.

If the dead-letter move succeeds but required watchdog record or projection
persistence fails, the item remains contained and the capped retry state is not
reported as cleared. RExecOp does not automatically reconcile that partial
persistence condition; operator inspection is required.

RExecOp owns `watchdog_decision.v0.1` and its runtime semantics. GovEngine owns
bounded supervisor-action admission. SCLite machinery verifies the artifact
projection; it does not supervise the worker.

Manual recovery intent is recorded explicitly:

```bash
rexecop --root /var/lib/rexecop watchdog manual-record \
  --action mark_stale \
  --reason operator_break_glass \
  --actor-ref operator:local-admin \
  --scope operation:op-123 \
  --operation op-123
```

This command records and admits a bounded supervisor action. It does not
requeue, restart, mutate the operation state or execute recovery.

## systemd worker example

```ini
[Unit]
Description=RExecOp read-only queue worker
After=network.target

[Service]
Type=simple
User=rexecop
Environment=REXECOP_ROOT=/var/lib/rexecop
Environment=REXECOP_SECRETS_FILE=/etc/rexecop/secrets.yaml
WorkingDirectory=/var/lib/rexecop
ExecStart=/opt/rexecop/.venv/bin/rexecop worker run --poll-interval 30 --watch-inbox --watchdog
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Use a reviewed wrapper script for recurring plan/start arguments. Avoid
embedding credentials, secret values or long shell pipelines in a unit file.
The unit must run as a dedicated account with the minimum required access to
the runtime root and host-owned secret provider.

## Operational notes

- FileStore queue and lock mechanics are single-host.
- The private claim lifecycle is implemented only for exact built-in File,
  Memory and SQLite stores; it is not a public custom-adapter contract.
- Expired-claim reconciliation is fenced by a fresh, strictly newer complete
  worker lease, requeues rather than replays eligible work, and does not renew
  claims or infer retry policy.
- Queue state lives under `<root>/queue/`; locks under `<root>/locks/`.
- Watchdog records live under `<root>/watchdog/`; dead letters under
  `<root>/dead_letter/`.
- Trigger deduplication, cooldown and timestamp-skew checks fail closed on
  inconsistent input.
- `auto_react: "plan_only"` may create a child plan after a successful source
  operation, but does not start that child.
- A scheduler never expands the authority of the selected runtime posture.

See [Operation lifecycle](operation-lifecycle.md),
[Runtime recovery](runtime-recovery-ops.md), and the
[Operator runbook](../OPERATOR_RUNBOOK.md).
