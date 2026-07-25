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
