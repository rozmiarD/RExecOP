# Operator scheduler pattern (Phase 12)

RExecOp does **not** ship a cron engine or recurring job DSL. Scheduling is **host-owned**:
use systemd timers, cron, or an external orchestrator to invoke RExecOp CLI commands.

GovEngine `GovSchedulerTick` is a **metadata contract** for governance projections — not a
scheduler implementation. See GovEngine `runtime_shell` docs.

## Patterns

### One-shot plan + start

```bash
OPERATION=$(rexecop plan --profile tecrax --env ~/.rexecop/env.yaml \
  --intent check_backup_status --target all_critical_vms --mode dry_run)
rexecop start --operation "$OPERATION"
```

### Worker drain (queue)

When apply operations are **approved** but queued (`max_concurrent_operations`, target lock):

```bash
rexecop worker run --once
# or continuous:
rexecop worker run --poll-interval 30
```

Equivalent without a long-running process:

```bash
rexecop queue --drain
```

### Trigger from webhook wrapper

```bash
echo '{"profile":"tecrax","env":"/path/env.yaml","intent":"check_backup_status","target":"all_critical_vms","mode":"dry_run","auto_start":true}' \
  | rexecop trigger
```

### File-drop inbox (with worker)

```bash
rexecop worker run --watch-inbox --poll-interval 60
```

Drop JSON files into `.rexecop/inbox/*.json` using the same shape as `trigger` stdin.

## systemd unit example

`/etc/systemd/system/rexecop-worker.service`:

```ini
[Unit]
Description=RExecOp queue worker
After=network.target

[Service]
Type=simple
User=rexecop
Environment=REXECOP_SECRETS_FILE=/home/rexecop/.rexecop/secrets.yaml
WorkingDirectory=/home/rexecop
ExecStart=/home/rexecop/.venv/bin/rexecop worker run --poll-interval 30 --watch-inbox
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Timer for recurring **plan** (not built-in recurrence DSL):

```ini
# /etc/systemd/system/rexecop-backup-check.timer
[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# /etc/systemd/system/rexecop-backup-check.service
[Service]
Type=oneshot
ExecStart=/bin/bash -c 'OPERATION=$(/home/rexecop/.venv/bin/rexecop plan ...); /home/rexecop/.venv/bin/rexecop start --operation "$OPERATION"'
```

## Lock and queue notes

- Target lock files live under `.rexecop/locks/` (advisory, single-host).
- Queue file: `.rexecop/queue/run_now.json`.
- Worker only starts operations in `approved` state on the queue; read-only plans still need `start` unless `trigger --auto-start`.

## Related

- [operation-lifecycle.md](operation-lifecycle.md)
- [OPERATOR_RUNBOOK.md](../OPERATOR_RUNBOOK.md)
