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
| `sclite/<op>/` | Authoritative SCLite artifact bundles | directory per operation |
| `queue/`, `locks/`, `inbox/` | Runtime coordination (not in StoragePort JSON API) | file drops |

`FileStore` uses `storage.atomic.atomic_write_text` (write temp + `os.replace`) for JSON
files to avoid torn reads on crash. Runtime directories are forced to mode `0700`; JSON,
receipt, lock, queue and SCLite files are forced to `0600`.

The stable certification is deliberately narrow: one active executor per runtime root,
enforced by the fenced execution lease. Set `REXECOP_EXECUTOR_POSTURE=single_executor`;
`rexecop doctor` blocks multi-worker or distributed-executor posture.

Operator backup and post-crash reconciliation are documented in
[runtime-recovery-ops.md](runtime-recovery-ops.md) (`backup create/restore`,
`runtime recover`).

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
- [evidence-model.md](evidence-model.md)
- [sclite-integration.md](sclite-integration.md)
