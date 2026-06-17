# Operator runbook

RExecOp **alpha** (`0.1.4a0`) — Regulated Execution Operations control-plane for
profile-defined workflows under GovEngine and SCLite.

This runbook covers installation, daily operations, staging setup, and safety checks.
For architecture and boundaries see [docs/](docs/) and [known-limitations.md](docs/known-limitations.md).

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Python | 3.11+ |
| RExecOp | Install from source (PyPI not published in alpha) |
| Tecrax profile | [`tecrax`](https://github.com/rozmiarD/tecrax) for `--profile tecrax` |
| GovEngine / SCLite | Pulled in via `pip install -e .` per `pyproject.toml` pins |
| Operator host | Shell access; network to targets when using `http_api` |

## Installation

```bash
git clone https://github.com/rozmiarD/RExecOP.git
cd RExecOP
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Tecrax profile (recommended for operators)
git clone https://github.com/rozmiarD/tecrax.git ../tecrax
pip install -e ../tecrax

rexecop version   # expect 0.1.4a0
python scripts/validate_public_truth.py   # docs + version alignment

# Optional: SQLite backend for operations/plans/evidence (SCLite bundles still on disk)
export REXECOP_STORAGE=sqlite
# or per invocation: rexecop --storage sqlite plan ...
```

## Secrets (never in git or `.rexecop/`)

Create `~/.rexecop/secrets.yaml` with mode `0600`:

```yaml
secrets:
  proxmox_base_url: https://pve-staging.example:8006
  proxmox_api_token: user@pam!token-id=uuid
  pbs_base_url: https://pbs-staging.example:8007
  pbs_api_token: pbs-token-value
```

```bash
export REXECOP_SECRETS_FILE=~/.rexecop/secrets.yaml
```

Alternative: `REXECOP_SECRET_PROXMOX_API_TOKEN`, etc. (see [connector-contract.md](docs/connector-contract.md)).

Environment YAML must use `secret_ref` / `base_url_secret_ref` — inline secrets are rejected at `plan`.

## Environment files

Copy a template out of git:

| Template | Use |
| --- | --- |
| `examples/environments/small-public-unit-proxmox.example.yaml` | Offline mock connectors (`fixture: tecrax_fixture`) |
| `examples/environments/small-public-unit-proxmox.staging.example.yaml` | Staging `http_api` |

Example operator path: `~/.rexecop/environments/small-public-unit-proxmox.yaml`

Adjust `targets`, action `path` values, and `secret_ref` names for your APIs.

## Standard read-only workflow

```bash
rexecop plan \
  --profile tecrax \
  --env ~/.rexecop/environments/small-public-unit-proxmox.yaml \
  --intent check_backup_status \
  --target all_critical_vms \
  --mode dry_run

rexecop start --operation <operation-id>
rexecop status --operation <operation-id>
rexecop validate --operation <operation-id>
rexecop history --operation <operation-id>
```

**Success criteria:**

- Final state `completed`
- `validate` → `passed: true`
- `.rexecop/sclite/<operation-id>/` contains artifact bundle
- Evidence and exports contain no plaintext tokens

Offline bootstrap (`tecrax-fixture` profile — **tests/bootstrap only**, not product profile):

```bash
pip install -e ../tecrax   # required for tecrax_fixture mock + internal actions

rexecop plan \
  --profile examples/profiles/tecrax-fixture/profile.yaml \
  --env examples/environments/small-public-unit-proxmox.example.yaml \
  --intent check_backup_status \
  --target all_critical_vms \
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

## Apply workflow (non-critical targets only)

1. Confirm GovEngine policy allows the intent on the target.
2. Plan with `--mode apply`.
3. If state is `waiting_for_approval`: `rexecop approve --operation <id>`.
4. `rexecop start --operation <id>`.
5. Inspect before/after state in evidence and SCLite receipt.

```bash
rexecop plan --profile tecrax --env <env> \
  --intent restart_zabbix_agent --target vm-zabbix-01 --mode apply
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

## Queue and concurrency

```bash
rexecop queue
```

Environment `safety` controls `max_concurrent_operations`, `target_lock_enabled`, and
`maintenance_windows` (see [operation-lifecycle.md](docs/operation-lifecycle.md)).

## Runtime layout (`.rexecop/`)

**Storage backends:** `file` (default) or `sqlite` (`REXECOP_STORAGE` / `--storage`).
Operations, plans, and evidence use JSON files (`file`) or `.rexecop/rexecop.db` (`sqlite`).
Queue, locks, receipts, approvals, and SCLite bundles always stay on disk under `.rexecop/`.
This is single-operator alpha storage — not multi-tenant or HA. SCLite bundles under
`.rexecop/sclite/` are authoritative for review; internal evidence is runtime telemetry only
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
| `profile not found: tecrax` | Install `tecrax` or use fixture path |
| `internal_action_not_registered:*` | Install domain package (e.g. `tecrax`) for internal workflow steps |
| `unsupported mock connector action` | Set `fixture: tecrax_fixture` on mock connectors or use `http_api` |
| `secret not found` | `REXECOP_SECRETS_FILE` or `REXECOP_SECRET_*` env |
| `mutating execution blocked` | GovEngine decision, approval state, maintenance window |
| `capability_undeclared` | Action missing from profile `connectors/*.yaml` |
| `connector disabled` | `enabled: false` in environment YAML |
| Queue stuck | `rexecop queue`; check target lock and `max_concurrent_operations` |

## Safety checklist (every environment)

- [ ] No secrets in git or committed `.rexecop/`
- [ ] Environment uses `secret_ref` only
- [ ] Read-only path validated before apply
- [ ] Apply tested on non-critical target first
- [ ] Target lock enabled for mutating workloads
- [ ] Maintenance windows configured if required
- [ ] Alpha limitations accepted ([known-limitations.md](docs/known-limitations.md))

## Related documents

- [OPERATOR_LAB_RUNBOOK.md](OPERATOR_LAB_RUNBOOK.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/safety-model.md](docs/safety-model.md)
- [docs/govengine-integration.md](docs/govengine-integration.md)
- [docs/sclite-integration.md](docs/sclite-integration.md)
- [CHANGELOG.md](CHANGELOG.md)
