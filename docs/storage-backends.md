# Storage and runtime roots

RExecOp persists operator runtime data under a runtime root. The fallback root is
`.rexecop/` in the current working directory, but operator workflows should prefer
an explicit root.

## Runtime root selection

| Selector | Effect |
| --- | --- |
| `--root <path>` | Explicit runtime root for one CLI invocation |
| `REXECOP_ROOT=<path>` | Explicit runtime root for the process environment |
| `--instance <name>` | Local named root under `./.rexecop/instances/<name>` when `--root` is omitted |
| `REXECOP_INSTANCE=<name>` | Environment equivalent of `--instance` |

Explicit `--root` / `REXECOP_ROOT` wins over named instances. Instance names are
tokens, not paths; use `--root` for absolute or operator-managed directories.

Initialize and check a root before using it:

```bash
rexecop --root /operator/rexecop-runtime init --guided
rexecop --root /operator/rexecop-runtime doctor
```

## Backend selection

| Backend | Env / CLI | Use |
| --- | --- | --- |
| `file` (default) | `REXECOP_STORAGE=file` or omit | Stable-certified single-host/single-executor runtime |
| `sqlite` | `REXECOP_STORAGE=sqlite` or `--storage sqlite` | Alpha storage evaluation; not stable-runtime certified |

Factory: `rexecop.storage.factory.create_store()`.

## FileStore (`file` backend)

| Path | Content | Write semantics |
| --- | --- | --- |
| `operations/*.json` | Operation envelopes | atomic replace via temp file |
| `plans/*.json` | OperationPlan snapshots | atomic replace |
| `evidence/<op>/*.json` | Internal evidence events | atomic replace |
| `receipts/*.json` | Non-authoritative export summaries | atomic replace |
| `approvals/*.json` | Manual approval stubs | atomic replace |
| `governance_claims/*.json` | Consumed decision-digest and nonce indexes | process-locked claim-once plus atomic replace |
| `permits/<op>/attempts/*.json` | Immutable runtime attempt permits | create once after governance claim |
| `permits/<op>/<step>.json` | Latest-per-step permit compatibility view | atomic replace |
| `permits/<op>/.attempt-permit.guard` | Stable per-operation permit guard | POSIX advisory process lock |
| `locks/*.lock` | Replaceable target-owner records | atomic replace under a distinct target guard |
| `locks/*.guard` | Stable per-target guards | POSIX advisory process lock |
| `sclite/<op>/` | Persisted bundle using SCLite canonical contracts | directory per operation |
| `queue/`, `locks/`, `inbox/` | Runtime coordination (not in StoragePort JSON API) | file drops |

`FileStore` uses `storage.atomic.atomic_write_text` (write temp + `os.replace`) for JSON
files to avoid torn reads on crash. Runtime directories are forced to mode `0700`; JSON,
receipt, lock, queue and SCLite files are forced to `0600`.

For cooperating processes on one POSIX host, the stable per-operation guard serializes
the immutable attempt-permit existence check and atomic publication, followed by the
replaceable latest-per-step projection. If projection fails after immutable publication,
the immutable permit remains authoritative for runtime retry handling: the same attempt
is rejected, while a new attempt may refresh the projection. A stable per-target guard,
separate from each replaceable `*.lock` record, serializes target-owner read, stale-owner
takeover, replacement and owner-checked release. The same active owner is idempotent, and
a delayed old-owner release cannot remove a newer record.

These guards provide POSIX single-host advisory locking for cooperating processes only.
They do not claim Windows, NFS or distributed-lock behavior; same-UID adversary
protection; power-loss durability; or two-file atomicity between an immutable permit and
its latest projection. Target-owner records do not add a fencing token, epoch or TTL;
the worker lease remains the runtime fence. Slash-to-underscore target-name collisions
and direct `AttemptJournal` create semantics remain outside this guarantee.

The stable certification is deliberately narrow: one active executor per runtime root,
enforced by the fenced execution lease. Set `REXECOP_EXECUTOR_POSTURE=single_executor`;
`rexecop doctor` blocks multi-worker or distributed-executor posture.

Operator backup and post-crash reconciliation are documented in
[runtime-recovery-ops.md](runtime-recovery-ops.md) (`backup create/restore`,
`runtime recover`).

## Major-line compatibility

Runtime roots are versioned operational state, not portable truth bundles. The
1.x policy is `alpha_root_requires_new_v1_root`: `rexecop init` fails with
`runtime_root_new_root_required` instead of overwriting a `0.x` manifest when
run by a 1.x binary. Keep the alpha root for audit, initialize a new empty 1.x
root and re-plan work there. Do not copy queue, lease, attempt or operation
lifecycle state across the boundary. See [public-api.md](public-api.md).

## SqliteStore (`sqlite` backend)

| Location | Content |
| --- | --- |
| `rexecop.db` tables `operations`, `plans`, `evidence_events` | JSON payloads identical to FileStore |
| `sclite/`, `receipts/`, `approvals/`, `queue/`, `locks/`, `inbox/` | **Still on disk** via delegated `FileStore` helpers |

SQLite stores **operation state**, **plans**, and **evidence event payloads** only.
SCLite bundles, receipt exports, queue entries, target locks, and inbox triggers remain
filesystem paths so review tooling and host-owned workers keep stable paths across backends.

`PRAGMA journal_mode=WAL` is enabled on open.
The database, WAL and shared-memory files are forced to mode `0600` inside a `0700`
runtime directory.

SQLite remains supported for alpha evaluation, but `rexecop doctor` reports it as a
stable-runtime blocker. Its auxiliary queue, lease, attempt and projection paths still use
the filesystem, so selecting SQLite does not create a fully transactional runtime backend.

## InMemoryStore (tests)

Operations, plans, and evidence live in RAM; SCLite output directory still uses on-disk
`FileStore` paths under the configured root.

## Related

- [architecture.md](architecture.md)
- [SCLite integration and evidence model](sclite-integration.md)
- [sclite-integration.md](sclite-integration.md)
