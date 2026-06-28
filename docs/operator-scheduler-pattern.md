# Operator scheduler pattern

RExecOp does **not** ship a cron engine or recurring job DSL. Scheduling is **host-owned**:
use systemd timers, cron, or an external orchestrator to invoke RExecOp CLI commands.

GovEngine `GovSchedulerTick` is a **metadata contract** for governance projections — not a
scheduler implementation. See GovEngine `runtime_shell` docs.

## Patterns

### One-shot plan + start

```bash
OPERATION=$(rexecop plan --profile tecrax --env ~/.rexecop/env.yaml \
  --intent collect_basic_host_inventory --target monitoring-host --mode dry_run)
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
echo '{"profile":"examples/profiles/runtime-fixture/profile.yaml","env":"examples/environments/runtime-fixture.policy.example.yaml","intent":"inspect_fixture_state","target":"fixture-target","mode":"dry_run","auto_start":true}' \
  | rexecop trigger
```

### File-drop inbox (with worker)

```bash
rexecop worker run --watch-inbox --poll-interval 60
```

Drop JSON files into `.rexecop/inbox/*.json` using the same shape as `trigger` stdin.
Inbox files may also carry a neutral trigger event. RExecOp evaluates only the
mechanics; event meaning and operation mapping come from the selected profile's
`triggers/trigger_rules.yaml`.

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

Trigger event decisions are limited to `plan_operation`, `ignore`, `escalate`,
`drop_duplicate`, and `cooldown_blocked`. A `plan_operation` decision creates a
normal operation plan through `OperationController.plan()` and records trigger
decision metadata/evidence on that operation. It does not start the operation.
Trigger rules may bind an operation target literally (`target` or
`catalog_target`) or by a neutral event-field path (`target_from` or
`catalog_target_from`, for example `subject`). Profiles own the meaning of those
fields; RExecOp only resolves the path and fails closed when it is missing or
ambiguous.
Before any `plan_operation` decision creates an operation, RExecOp submits a
bounded GovEngine `TriggerPlanningRequest` built only from event/rule digests,
the trigger decision, and the requested intent/mode. Mutating modes, missing
rule digests, raw event data, private target data, and unsupported decisions
fail closed before operation planning. Record-only decisions such as
`escalate`, `drop_duplicate`, `cooldown_blocked`, and `ignore` are admitted as
non-executing records.

Each trigger decision is also projected into the SCLite `trigger_decision.v0.1`
truth shape. That artifact stores only bounded event/rule/admission digests and
an optional child operation id; it does not include raw payloads, authorize
planning, schedule work, or execute anything.

### Worker watchdog

The worker can supervise RExecOp's own runtime mechanics:

```bash
rexecop worker run --watch-inbox --watchdog \
  --stale-inbox-seconds 3600 \
  --stale-operation-seconds 3600 \
  --inbox-retry-budget 3 \
  --poll-interval 60
```

The watchdog is not infrastructure monitoring and does not interpret profile
domain health. It records bounded worker heartbeats, queue depth, and inbox
dead-letter decisions under `.rexecop/watchdog/`. When enabled with
`--watch-inbox`, inbox files older than `--stale-inbox-seconds` are moved to
`.rexecop/dead_letter/` before execution. Failed inbox files consume a bounded
retry budget and then move to dead-letter. Stale active operations produce a
`block_autostart` watchdog record; watchdog does not mutate the operation FSM to
hide the stuck state. Watchdog records include file names, operation ids, reasons
and bounded timing metadata; they do not copy trigger payloads.

Watchdog records are operational runtime records. RExecOp writes bounded SCLite
projection metadata for the future `evidence_contract` path, but the projection
is not itself SCLite authority.

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
ExecStart=/home/rexecop/.venv/bin/rexecop worker run --poll-interval 30 --watch-inbox --watchdog
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
- Watchdog records live under `.rexecop/watchdog/`; dead-lettered trigger files
  live under `.rexecop/dead_letter/`.
- Worker only starts operations in `approved` state on the queue; read-only plans still need `start` unless `trigger --auto-start`.
- Trigger payloads may opt into `auto_react: "plan_only"`. After the source
  operation completes, RExecOp may create a reaction chain and child operation
  plan, but the worker does not start that child automatically.
- Trigger events use deterministic event digest, dedupe key, cooldown key and
  bounded timestamp-skew checks. Unsafe or inconsistent event time fails closed.

## Related

- [operation-lifecycle.md](operation-lifecycle.md)
- [OPERATOR_RUNBOOK.md](../OPERATOR_RUNBOOK.md)
