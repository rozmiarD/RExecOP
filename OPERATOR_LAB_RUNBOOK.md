# Operator lab runbook (Phase 11)

RExecOp `0.1.5a0` — validate neutral core, plugin boundaries, and read-only paths before apply.

## Prerequisites

| Item | Command / check |
|------|-----------------|
| Python 3.11+ | `python --version` |
| RExecOp | `pip install -e ".[dev]"` from repo root |
| Tecrax (domain plugins) | `pip install -e ../tecrax` |
| GovEngine / SCLite | Installed via rexecop dependencies |
| Secrets file | `~/.rexecop/secrets.yaml` mode `0600` |

```bash
export REXECOP_SECRETS_FILE=~/.rexecop/secrets.yaml
rexecop version    # 0.1.5a0
export REXECOP_STORAGE=sqlite   # optional Phase 13.1 backend
```

## Lab checklist

### 1. Core boundary

- [ ] `python scripts/validate_public_truth.py` passes
- [ ] `ruff check . --exclude tecrax` passes
- [ ] `rg 'vm-101|proxmox|pbs|zabbix' src/rexecop` returns **no matches**
- [ ] `rg 'import tecrax' src/rexecop` returns **no matches**

### 2. Secrets hygiene

- [ ] No plaintext tokens in git or committed `.rexecop/`
- [ ] Environment YAML uses `secret_ref` / `base_url_secret_ref` only
- [ ] After a run: `rg -i 'api_key|token|password' .rexecop/` shows only `[REDACTED]` or no hits

### 3. http_api-only golden path (no domain internals)

Uses `examples/profiles/http-health-fixture` — single connector step, no Tecrax internal actions.

```bash
# Run the CI-equivalent test locally:
pytest tests/test_http_health_check_e2e.py -q
```

Manual path: copy a staging env with `backend: http_api` pointing at your `/health` endpoint.

- [ ] `plan` + `start` → `completed`
- [ ] `validate` → `passed: true`, rule `http_health_check.probe_ok`

### 4. Tecrax offline fixture (bootstrap)

Requires `tecrax` installed (`rexecop.internal_actions` + `tecrax_fixture` mock).

```bash
rexecop plan \
  --profile examples/profiles/tecrax-fixture/profile.yaml \
  --env examples/environments/small-public-unit-proxmox.example.yaml \
  --intent check_backup_status \
  --target all_critical_vms \
  --mode dry_run

rexecop start --operation <id>
rexecop validate --operation <id>
```

- [ ] Final state `completed`
- [ ] `.rexecop/sclite/<id>/` contains bundle artifacts
- [ ] No secrets in evidence JSON

### 5. Tecrax product profile (optional)

```bash
rexecop plan --profile tecrax --env <env> \
  --intent check_backup_status --target all_critical_vms --mode dry_run
rexecop start --operation <id>
```

### 6. Staging HTTP (CI pattern)

```bash
pytest tests/test_staging_connectors_e2e.py -q
```

Uses local HTTP stub — same shape as production `http_api` config.

### 7. Alpha sign-off

- [ ] Read [docs/known-limitations.md](docs/known-limitations.md)
- [ ] Accept no background worker until Phase 12
- [ ] Apply only on non-critical targets with explicit approve
- [ ] `StaticGovEngineAdapter` used in tests only; production path uses `GovEngineClient`

## Evidence vs SCLite truth

| Location | Role |
|----------|------|
| `.rexecop/evidence/` | Redacted runtime events (operator telemetry) |
| `.rexecop/sclite/<op>/` | Authoritative SCLite bundle for review |
| `.rexecop/receipts/` | Export summary — not truth authority |

## Related

- [OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/profile-contract.md](docs/profile-contract.md)
