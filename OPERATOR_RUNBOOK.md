# Operator runbook

RExecOp **alpha** (`0.2.12a0` source line) — Regulated Execution Operations control-plane for
profile-defined workflows under GovEngine and SCLite.

This runbook covers installation, daily operations, staging setup, and safety checks.
For architecture and boundaries see [docs/](docs/) and [known-limitations.md](docs/known-limitations.md).

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Python | 3.11+ |
| RExecOp | Install from source or internal wheel (see [docs/distribution.md](docs/distribution.md)) |
| Tecrax profile | [`tecrax`](https://github.com/rozmiarD/tecrax) for `--profile tecrax` |
| GovEngine / SCLite | Install coordinated GovEngine source first; SCLite resolves from the package range |
| Operator host | Shell access; network to targets when using real connector endpoints |

## Installation

```bash
git clone https://github.com/rozmiarD/RExecOP.git
cd RExecOP
python -m venv .venv && source .venv/bin/activate
git clone https://github.com/rozmiarD/GovEngine.git ../govengine
pip install -e ../govengine
pip install -e ".[dev]"

# Tecrax profile (recommended for operators)
git clone https://github.com/rozmiarD/tecrax.git ../tecrax
pip install -e ../tecrax

rexecop version   # expect 0.2.12a0 from the coordinated source checkout
python scripts/validate_public_truth.py   # docs + version alignment
python scripts/validate_first_run_smoke.py # no-I/O init/doctor/explain/plan smoke

# Optional: SQLite backend for operations/plans/evidence (SCLite bundles still on disk)
export REXECOP_STORAGE=sqlite
# or per invocation: rexecop --storage sqlite plan ...
```

## Runtime root and first-run diagnostics

Use an explicit root for operator work. The default remains `./.rexecop`, but
`--root` or `REXECOP_ROOT` avoids accidental writes from the wrong working
directory. Named local instances are available through `--instance` or
`REXECOP_INSTANCE`; they resolve under `./.rexecop/instances/<name>` unless
`--root` is supplied.

```bash
export REXECOP_ROOT=~/rexecop-runtime
rexecop init --guided

rexecop doctor \
  --profile examples/first-run-demo/profile/profile.yaml \
  --env examples/first-run-demo/environment.yaml \
  --catalog examples/first-run-demo/catalog.yaml

rexecop profile lint \
  --profile examples/first-run-demo/profile/profile.yaml \
  --track readonly

rexecop env lint \
  --env examples/first-run-demo/environment.yaml \
  --profile examples/first-run-demo/profile/profile.yaml
```

For a complete no-I/O walkthrough, see [docs/first-run.md](docs/first-run.md).

## Secrets (never in git or `.rexecop/`)

Create `~/.rexecop/secrets.yaml` with mode `0600`:

```yaml
secrets:
  fixture_base_url: https://api-staging.example.invalid
  fixture_api_token: REPLACE_ME
  fixture_ca_file: /path/outside/repo/ca.pem
```

```bash
chmod 0600 ~/.rexecop/secrets.yaml
export REXECOP_SECRETS_FILE=~/.rexecop/secrets.yaml
```

Alternative: `REXECOP_SECRET_FIXTURE_API_TOKEN`, etc. (see [connector-contract.md](docs/connector-contract.md)).

Environment YAML must use `secret_ref` / `base_url_secret_ref` — inline secrets are rejected at `plan`.
Use `rexecop env lint --env <environment.yaml> --profile <profile.yaml>` before
planning a new operator environment.

When the environment declares secret refs, also run:

```bash
rexecop secrets doctor --env <environment.yaml> [--secrets-file ~/.rexecop/secrets.yaml]
```

See [docs/secrets-operator.md](docs/secrets-operator.md). The command checks ref
resolution and file policy without printing secret values.

## Environment files

Copy a template out of git:

| Template | Use |
| --- | --- |
| `examples/environments/runtime-fixture.example.yaml` | Offline no-I/O `static_fixture` connector |
| `examples/environments/runtime-fixture.policy.example.yaml` | Offline no-I/O fixture with fail-closed policy pack |
| `examples/environments/runtime-fixture.staging.example.yaml` | Generic staging `http_api` |

Example operator path: `~/.rexecop/environments/runtime-fixture.staging.yaml`

Adjust `targets`, action `path` values, and `secret_ref` names for your APIs.

## Standard read-only workflow

```bash
rexecop --root ~/rexecop-runtime plan \
  --profile /path/to/RExecOP/examples/profiles/runtime-fixture/profile.yaml \
  --env ~/.rexecop/environments/runtime-fixture.staging.yaml \
  --intent inspect_fixture_state \
  --target fixture-target \
  --mode dry_run

rexecop --root ~/rexecop-runtime start --operation <operation-id>
rexecop --root ~/rexecop-runtime status --operation <operation-id>
rexecop --root ~/rexecop-runtime validate --operation <operation-id>
rexecop --root ~/rexecop-runtime history --operation <operation-id>
```

**Success criteria:**

- Final state `completed`
- `validate` → `passed: true`
- `.rexecop/sclite/<operation-id>/` contains artifact bundle
- Evidence and exports contain no plaintext tokens

Offline bootstrap (`runtime-fixture` profile — **tests/bootstrap only**, not product profile):

```bash
rexecop plan \
  --profile examples/profiles/runtime-fixture/profile.yaml \
  --env examples/environments/runtime-fixture.example.yaml \
  --intent inspect_fixture_state \
  --target fixture-target \
  --mode dry_run
```

Generic http_api-only path (no domain internals in core):

```bash
rexecop plan \
  --profile examples/profiles/http-health-fixture/profile.yaml \
  --env <your-http-health-env.yaml> \
  --intent http_health_check \
  --target api_primary \
  --mode dry_run
```

See [OPERATOR_LAB_RUNBOOK.md](OPERATOR_LAB_RUNBOOK.md) for the full lab checklist.

## Runtime triage and recovery

When queue, worker, or operation state looks wrong:

```bash
rexecop --root ~/rexecop-runtime ops
rexecop --root ~/rexecop-runtime runtime status --json
rexecop explain-error <operation-id-or-dead-letter-or-watchdog-ref>
```

After host restart or worker crash:

```bash
rexecop --root ~/rexecop-runtime runtime recover --json
```

Before invasive maintenance on the runtime root:

```bash
rexecop --root ~/rexecop-runtime backup create --output ~/backups/rexecop-runtime.tar.gz
```

Full reference: [docs/runtime-recovery-ops.md](docs/runtime-recovery-ops.md).

M2 decision surfaces before mutating work:

```bash
rexecop operation explain --operation <id>
rexecop operation review --operation <id>
rexecop operation diff --operation <id>
rexecop policy explain --profile ... --env ... --intent ... --target ...
```

## Apply workflow (fixture or non-critical targets only)

1. Confirm GovEngine policy allows the intent on the target.
2. Plan with `--mode apply`.
3. If state is `waiting_for_approval`: `rexecop approve --operation <id>`.
4. `rexecop start --operation <id>`.
5. Inspect before/after state in evidence and SCLite receipt.

```bash
rexecop plan --profile examples/profiles/runtime-fixture/profile.yaml \
  --env examples/environments/runtime-fixture.policy.example.yaml \
  --intent apply_fixture_change --target fixture-target --mode apply
rexecop approve --operation <id>   # if required
rexecop start --operation <id>
```

## Pause, resume, cancel, retry

```bash
rexecop pause --operation <id>    # only at pause_safe steps
rexecop resume --operation <id>
rexecop cancel --operation <id>
rexecop retry --operation <id>    # after failed, if profile allows
```

## Rollback drill

After a **failed** apply with rollback defined in the workflow:

```bash
rexecop rollback --operation <id>
```

Confirm rollback steps ran (typically `dry_run` mode) and evidence records the marker.

## Escalation

```bash
rexecop escalate --operation <id>
```

Review the escalation package JSON (operation state, failed step, GovEngine summary, safe next options).
Escalation options are **descriptive** — they are not auto-executed.

## Queue, worker, and triggers

When `start` cannot run immediately (target lock, `max_concurrent_operations`), approved
operations wait with `metadata.queue.status = pending`.

```bash
rexecop queue                      # inspect backlog
rexecop queue --drain              # one-shot drain without a worker loop
rexecop worker run --once          # poll once and start queued operations
rexecop worker run --poll-interval 30 --watch-inbox
```

External schedulers (systemd, cron) invoke the CLI — RExecOp does not ship a cron engine.
See [operator-scheduler-pattern.md](docs/operator-scheduler-pattern.md).

Create operations from automation:

```bash
echo '{"profile":"examples/profiles/runtime-fixture/profile.yaml","env":"examples/environments/runtime-fixture.policy.example.yaml","intent":"inspect_fixture_state","target":"fixture-target","mode":"dry_run","auto_start":true}' \
  | rexecop trigger
```

## Queue and concurrency

```bash
rexecop queue
```

Environment `safety` controls `max_concurrent_operations`, `target_lock_enabled`, and
`maintenance_windows` (see [operation-lifecycle.md](docs/operation-lifecycle.md)).

## Runtime layout (`.rexecop/`)

**Storage backends:** `file` (default) or `sqlite` (`REXECOP_STORAGE` / `--storage`).
**Root selection:** `--root`, `REXECOP_ROOT`, `--instance`, `REXECOP_INSTANCE`, or default
`./.rexecop`.
Operations, plans, and evidence use JSON files (`file`) or `<root>/rexecop.db` (`sqlite`).
Queue, locks, receipts, approvals, and SCLite bundles always stay on disk under the
selected runtime root.
This is single-operator alpha storage — not multi-tenant or HA. SCLite bundles under
`sclite/` are authoritative for review; internal evidence is runtime telemetry only
(see [architecture.md](docs/architecture.md)).

| Path | Content |
| --- | --- |
| `operations/` | Operation envelope (`file` backend only) |
| `rexecop.db` | Operations, plans, evidence (`sqlite` backend) |
| `evidence/` | Redacted internal events |
| `sclite/<op>/` | Authoritative SCLite bundle |
| `receipts/` | Export summary |
| `approvals/` | Manual approval stubs |

Directory is gitignored — back up operator-side if retention is required.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `profile not found: tecrax` | Install `tecrax` for the domain profile or use a fixture profile path |
| `internal_action_not_registered:*` | Install domain package (e.g. `tecrax`) for internal workflow steps |
| `unsupported connector action` | Check profile connector capabilities and environment action names |
| `secret not found` | `REXECOP_SECRETS_FILE` or `REXECOP_SECRET_*` env |
| `mutating execution blocked` | GovEngine decision, approval state, maintenance window |
| `capability_undeclared` | Action missing from profile `connectors/*.yaml` |
| `connector disabled` | `enabled: false` in environment YAML |
| Queue stuck | `rexecop ops`; `rexecop queue`; `rexecop queue --drain` or `rexecop worker run --once` |
| Post-crash duplicate IO / stale lease | `rexecop runtime recover --json`; inspect `explain-error` for watchdog refs |
| Wrong runtime data path | Run `rexecop doctor`; prefer `--root` / `REXECOP_ROOT` for operator work |

## Safety checklist (every environment)

- [ ] No secrets in git or committed `.rexecop/`
- [ ] Environment uses `secret_ref` only
- [ ] `rexecop doctor`, `rexecop env lint`, `rexecop profile lint`, and `rexecop secrets doctor` pass (when using secret refs)
- [ ] Catalog targets checked with `rexecop operations unavailable` when applicability is unclear ([docs/operator-catalog.md](docs/operator-catalog.md))
- [ ] Read-only path validated before apply
- [ ] Apply tested on non-critical target first
- [ ] Target lock enabled for mutating workloads
- [ ] Maintenance windows configured if required
- [ ] Alpha limitations accepted ([known-limitations.md](docs/known-limitations.md))

## Related documents

- [OPERATOR_LAB_RUNBOOK.md](OPERATOR_LAB_RUNBOOK.md)
- [docs/profile-developer-surface.md](docs/profile-developer-surface.md)
- [docs/secrets-operator.md](docs/secrets-operator.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/safety-model.md](docs/safety-model.md)
- [docs/govengine-integration.md](docs/govengine-integration.md)
- [docs/sclite-integration.md](docs/sclite-integration.md)
- [CHANGELOG.md](CHANGELOG.md)
