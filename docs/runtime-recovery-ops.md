# Runtime recovery and triage

RExecOp exposes bounded operator commands for runtime health, failure triage,
startup recovery, and store backup. These surfaces inspect or reconcile local
runtime state — they do not replace GovEngine admission or SCLite contract
verification.

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

Before connector IO, RExecOp preallocates `attempt_id`, then writes and
verifies `rexecop.runtime_attempt_permit.v0.1`. The permit binds the current operation
revision, attempt, plan/spec digests, target, mode, lease and expiry. When a canonical
GovEngine authority is configured, RExecOp first verifies the signed
`GovernanceDecision`, checks exact attempt/runtime/lease/fencing/spec/payload/scope/
inventory bindings, and atomically claims both the decision digest and nonce. Only then
does it persist `attempt started`. After that durable write, RExecOp revalidates
the permit, current lease, fencing and runtime bindings immediately before
connector IO. A failed final check terminates the attempt as failed without
invoking the connector; it is not an indeterminate outcome.

Mutating connector IO has no unsigned compatibility fallback. Read-only operations may
still use the explicitly labelled `legacy_read_only` binding while callers migrate to
the signed-decision authority port; that label is not a governance authenticity claim.
Recovery never clears governance claims, so an indeterminate attempt cannot reuse its
old decision. The runtime permit remains a RExecOp freshness/binding record, not a
GovEngine policy decision or canonical SCLite artifact.

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
rexecop --root "$REXECOP_ROOT" backup create --output "$BACKUP_DIR/rexecop-2026-07-04.tar"
rexecop --root "$RESTORE_ROOT" backup restore --archive "$BACKUP_DIR/rexecop-2026-07-04.tar"
```

- `backup create` produces an uncompressed USTAR `.tar` containing
  regular (`REGTYPE`) members only, regardless of the output name. Use `.tar`:
  accepted `.tgz` and `.tar.gz` names are not compressed. The source must contain
  a valid, compatible, real `runtime_manifest.json`; selected trees may contain
  only real directories and regular files, with symbolic links and other types
  rejected. Archive and sidecar outputs must be outside the runtime root.
- Creation copies the selected files to a private staging snapshot, validates
  the runtime manifest, and runs the configured sensitive-filename scan over
  that snapshot. A scan finding blocks publication.
- The sidecar (`rexecop.runtime_backup.v0.1`, at most 1 MiB) binds the exact
  archive basename, exact member set and count, and SHA-256 for every member.
  It does **not** provide an archive-wide digest, authenticity, or a signature.
- Limits are: runtime manifest 1 MiB; sidecar 1 MiB; at most 10,000 files;
  raw archive 512 MiB; each member 64 MiB; total expanded content 256 MiB; and
  a member path of at most 4096 UTF-8 bytes and 255 bytes per component, subject
  to the USTAR and portable-name rules.
- `backup restore` requires the sidecar and accepts only the strict USTAR
  regular-member subset. It rejects extensions (including PAX and GNU), AREG
  and other non-regular/link/device members, ambiguous or portable-colliding
  paths, non-canonical or incorrect checksums, non-zero member padding,
  non-zero trailing data (including concatenated streams), and any
  manifest/member/digest/runtime-manifest compatibility mismatch. Zero padding
  and terminating zero blocks are valid.
- The restore target must be absent or strictly empty. Extraction occurs in a
  private sibling staging directory and the whole directory is promoted only
  after validation. Existing-empty replacement is qualified to Linux; this is
  not a cross-platform replacement guarantee.
- On ordinary successful creation the sidecar is made visible before the archive;
  the archive is the visibility marker. This is not an atomic two-file
  publication guarantee.

Backups are operator-owned artifacts outside git. The archive and sidecar are
`0600`; restored directories and files are `0700` and `0600`. They may contain
sensitive runtime metadata or evidence; redaction is not a safety boundary.

Backup/restore does **not** claim archive authenticity, signatures, or an
archive-wide digest; protection from a same-UID adversary; or power-loss/crash
durability, because directory fsync is absent.

Restore auto-discovers the adjacent generated sidecar; use `--manifest` when it
was relocated. Creation does not overwrite an existing archive or sidecar.

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
| `backup *` | Operator store snapshots | Canonical SCLite bundle export |
| `explain-error` | Mapping refs to next actions | Automatic remediation |
| `retry` | Re-attempt failed operation when profile retry policy allows | Connector replay without clearing failure cause |
