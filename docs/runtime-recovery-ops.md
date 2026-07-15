# Runtime recovery and triage

RExecOp exposes bounded operator commands for runtime health, failure triage,
startup recovery, and store backup. These surfaces inspect or reconcile local
runtime state — they do not replace GovEngine admission or SCLite truth.

## Triage commands

```bash
rexecop --root /operator/rexecop-runtime runtime status --json
rexecop --root /operator/rexecop-runtime ops
rexecop --root /operator/rexecop-runtime dead-letter list
rexecop --root /operator/rexecop-runtime dead-letter show <name>
rexecop --root /operator/rexecop-runtime locks list
rexecop explain-error <operation-id|dead-letter-name|watchdog-record-id>
```

| Command | Schema / output | Purpose |
| --- | --- | --- |
| `runtime status --json` | `rexecop.runtime_status.v0.1` | Queue depth, active operations, locks, dead-letter summary |
| `ops` | `rexecop.ops.v0.1` | Aggregated blockers, action-required operations, stale locks; exit `1` when blockers present |
| `dead-letter list` | `rexecop.dead_letter_list.v0.1` | Watchdog-moved inbox payloads |
| `dead-letter show` | `rexecop.dead_letter_show.v0.1` | One redacted dead-letter item |
| `locks list` | `rexecop.locks_list.v0.1` | Advisory target locks and stale holders |
| `explain-error` | `rexecop.explain_error.v0.1` | Failure class, bounded summary, safe next actions |
| `runtime reconstruct-status --json` | `rexecop.runtime_reconstruction.v0.1` | Read-only reconstruction readiness and blockers |

`explain-error` accepts an operation id, dead-letter file name, or watchdog record
id under `<root>/watchdog/`. For watchdog records it may include a redacted
`govengine_supervisor_explanation` from `explain_supervisor_action()` — see
[govengine-integration.md](govengine-integration.md#supervisor-explanations-g2).

## Startup recovery

After process crash, host restart, or worker interruption:

```bash
rexecop --root /operator/rexecop-runtime runtime recover --json
```

`runtime recover` (also invoked automatically when `worker run` starts):

- clears stale worker leases;
- releases stale advisory target locks;
- marks interrupted active operations `failed` with a recovery transition;
- converts connector attempts left `started` into deterministic `indeterminate` records;
- repairs or blocks on terminal operations missing receipt artifacts;
- reconciles pending terminal SCLite projections without re-running connector IO.

Output schema: `rexecop.runtime_recovery.v0.1`. Recovery blockers are written
under `<root>/recovery_blockers/` when receipt repair cannot proceed safely.

Recovery does **not** re-run connector IO. Plan/start/trigger idempotency keys detect
logical replay and key drift; by themselves they do **not** prevent duplicate backend
invocation. Connector attempts are persisted before IO. A process loss after IO but before
the durable result becomes `outcome_indeterminate`; side-effectful work is never retried
automatically and requires explicit reconciliation.

Immediately before connector IO, RExecOp preallocates `attempt_id`, then writes and
verifies `rexecop.runtime_attempt_permit.v0.1`. The permit binds the current operation
revision, attempt, plan/spec digests, target, mode, lease and expiry. When a canonical
GovEngine authority is configured, RExecOp first verifies the signed
`GovernanceDecision`, checks exact attempt/runtime/lease/fencing/spec/payload/scope/
inventory bindings, and atomically claims both the decision digest and nonce. Only then
does it persist `attempt started` before connector IO.

Mutating connector IO has no unsigned compatibility fallback. Read-only operations may
still use the explicitly labelled `legacy_read_only` binding while callers migrate to
the signed-decision authority port; that label is not a governance authenticity claim.
Recovery never clears governance claims, so an indeterminate attempt cannot reuse its
old decision. The runtime permit remains a RExecOp freshness/binding record, not a
GovEngine policy decision or SCLite truth artifact.

## Runtime-store reconstruction status

Before or after recovery, inspect whether the local runtime store has enough
records to rebuild RExecOp's operational view without mutating it:

```bash
rexecop --root /operator/rexecop-runtime runtime reconstruct-status --json
```

Output schema: `rexecop.runtime_reconstruction.v0.1`.

The command is read-only. It checks operation records, plan records, terminal
receipt exports, evidence directories, SCLite bundle refs, idempotency metadata,
recovery blockers and auto-reaction chain refs. It reports:

- `reconstructable` when all required local runtime inputs are present;
- `needs_recovery` when active states require `runtime recover` before a
  reconstruction claim;
- `partial` when the runtime can be rebuilt but non-authoritative refs are
  incomplete;
- `blocked` when required runtime records are missing or invalid.

Reconstruction status does **not** repair state, export receipts, execute
connectors, recompute GovEngine admission, or canonicalize SCLite artifacts.

## Runtime store backup

```bash
rexecop --root /operator/rexecop-runtime backup create --output /operator/backups/rexecop-2026-07-04.tar.gz
rexecop --root /operator/rexecop-runtime backup restore --archive /operator/backups/rexecop-2026-07-04.tar.gz
```

- `backup create` tarballs the runtime store after a secret scan; blocked when
  scan finds candidates.
- A sidecar manifest (`rexecop.runtime_backup.v0.1`) records archive digest and
  included paths.
- `backup restore` requires the manifest and restores into the configured
  runtime root only when the target layout is empty or explicitly safe to replace.

Backups are operator-owned artifacts outside git. They may contain operation
metadata and redacted evidence — treat as sensitive (`0600`).

## Manual watchdog records

Governed manual recovery decisions (no automatic repair execution):

```bash
rexecop watchdog manual-record \
  --action renew_lease \
  --reason stale_worker \
  --actor-ref operator:alice \
  --scope runtime:local \
  --operation <operation-id>
```

See [operator-scheduler-pattern.md](operator-scheduler-pattern.md) for worker,
watchdog, and inbox interaction.

## Operator flow

```text
ops / runtime status
  -> explain-error <ref>
  -> runbook show <intent> (context)
  -> retry <operation-id> (when profile allows; no connector replay from recover)
  -> runtime recover (after restart)
  -> backup create (before invasive maintenance)
  -> plan / start only when blockers are understood
```

### Fixture failure injection (lab only)

For automated retry drills on the neutral `static_fixture` backend, tests and
`validate_operator_journeys.py` may set:

```bash
export REXECOP_STATIC_FIXTURE_FAILURES='{"fixture_source:read_fixture_state":{"count":5,"error_class":"transient_connector_error"}}'
rexecop --root "$REXECOP_ROOT" start --operation <id>
```

Effects:

- Applies only to `backend: static_fixture` with `fixture_only: true`.
- Scoped to one CLI process; does not affect production connectors.
- After auto-retry exhaustion, `retry --operation` clears the path when failures are removed.

Do **not** use this on operator hosts as a substitute for real incident response.

## Authority boundaries

| Surface | Owns | Does not own |
| --- | --- | --- |
| Triage CLI | Bounded runtime inspection and failure classes | Policy verdicts |
| `runtime recover` | Store reconciliation, lease/lock cleanup | Connector replay |
| `backup *` | Operator store snapshots | SCLite truth export |
| `explain-error` | Mapping refs to next actions | Automatic remediation |
| `retry` | Re-attempt failed operation when profile retry policy allows | Connector replay without clearing failure cause |
