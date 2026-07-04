# Operator lab runbook

RExecOp `0.2.12a0` source line — validate neutral core, plugin boundaries, read-only paths, and the full
profile → GovEngine → SCLite emission path before apply.

Runtime data is written to the selected runtime root. Use `--root` / `REXECOP_ROOT`
for lab work so artifacts stay isolated; the fallback remains `.rexecop/` in the
current working directory.

## Prerequisites

| Item | Command / check |
|------|-----------------|
| Python 3.11+ | `python --version` |
| RExecOp | `pip install -e ".[dev]"` from repo root (see [docs/distribution.md](docs/distribution.md)) |
| Tecrax (optional domain profile) | `pip install -e ../tecrax` only for Tecrax-specific checks |
| GovEngine / SCLite | Installed via rexecop dependencies |
| Secrets file | `~/.rexecop/secrets.yaml` mode `0600` |

```bash
export REXECOP_SECRETS_FILE=~/.rexecop/secrets.yaml
export REXECOP_ROOT=~/lab/rexecop-runtime
rexecop version    # 0.2.12a0
export REXECOP_STORAGE=sqlite   # optional SQLite backend for operations/plans/evidence
python scripts/validate_public_truth.py
python scripts/validate_first_run_smoke.py
```

## Lab checklist

### 1. Core boundary

- [ ] `python scripts/validate_public_truth.py` passes
- [ ] `ruff check . --exclude tecrax` passes
- [ ] `rg 'vm-101|proxmox|pbs|zabbix|tecrax' src/rexecop` returns **no matches**
- [ ] `rg 'import tecrax' src/rexecop` returns **no matches**

### 2. Secrets hygiene

- [ ] No plaintext tokens in git or committed `.rexecop/`
- [ ] Environment YAML uses `secret_ref` / `base_url_secret_ref` only
- [ ] After a run: `rg -i 'api_key|token|password' "${REXECOP_ROOT:-.rexecop}"/` shows only `[REDACTED]` or no hits

### 2b. First-run readiness

Uses `examples/first-run-demo`; no domain package, endpoint, or secret is required.

```bash
rexecop --root "$REXECOP_ROOT" init --guided
rexecop --root "$REXECOP_ROOT" doctor \
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

- [ ] `doctor` returns `status: passed`
- [ ] Profile and environment lint return `status: passed`
- [ ] `python scripts/validate_first_run_smoke.py` passes

### 3. http_api-only golden path (no domain internals)

Uses `examples/profiles/http-health-fixture` — single connector step, no Tecrax internal actions.

```bash
pytest tests/test_http_health_check_e2e.py -q
```

Manual path: copy a staging env with `backend: http_api` pointing at your `/health` endpoint.

- [ ] `plan` + `start` → `completed`
- [ ] `validate` → `passed: true`, rule `http_health_check.probe_ok`

### 4. Neutral runtime fixture (bootstrap)

Uses `examples/profiles/runtime-fixture`; no domain package or real endpoint is required.

```bash
rexecop --root "$REXECOP_ROOT" plan \
  --profile examples/profiles/runtime-fixture/profile.yaml \
  --env examples/environments/runtime-fixture.example.yaml \
  --intent inspect_fixture_state \
  --target fixture-target \
  --mode dry_run

rexecop --root "$REXECOP_ROOT" start --operation <id>
rexecop --root "$REXECOP_ROOT" validate --operation <id>
```

- [ ] Final state `completed`
- [ ] `"$REXECOP_ROOT"/sclite/<id>/` contains bundle artifacts
- [ ] `metadata.policy_verdict.decision` is `allow` for dry_run readonly (default lab env ships `policy_pack`)
- [ ] No secrets in evidence JSON

### 4b. Policy pack on lab fixture

Use `examples/environments/runtime-fixture.policy.example.yaml` (readonly
static fixture path with `policy_pack`). The base `runtime-fixture.example.yaml`
stays without a pack so apply/mutation tests keep a neutral fixture.

```bash
pytest tests/test_readonly_vertical_slice_e2e.py tests/test_connector_policy_engine.py -q
```

Manual lab:

```bash
rexecop --root "$REXECOP_ROOT" plan \
  --profile examples/profiles/runtime-fixture/profile.yaml \
  --env examples/environments/runtime-fixture.policy.example.yaml \
  --intent inspect_fixture_state --target fixture-target --mode dry_run
rexecop --root "$REXECOP_ROOT" start --operation <id>
```

- [ ] Readonly `inspect_fixture_state` completes with `policy_verdict.decision: allow`
- [ ] Connector policy denies `ssh_readonly` on critical targets (unit tests)

### 5. Tecrax product profile (optional)

```bash
rexecop plan --profile tecrax --env <env> \
  --intent collect_basic_host_inventory --target <operator-target> --mode dry_run
rexecop start --operation <id>
```

### 6. Staging HTTP lab (`http_api`)

**Local stub (no secrets, operator host):**

```bash
python scripts/run_staging_http_lab.py
# optional: --workdir /tmp/rexecop-staging-http-lab
```

Starts embedded domain-neutral API stub on `127.0.0.1`, runs `plan` → `start` → `validate`
for `inspect_fixture_state` / `fixture-target` / `dry_run` using
`examples/environments/runtime-fixture.staging.lab.example.yaml`.

**Real staging endpoints:**

```bash
cp examples/environments/runtime-fixture.staging.example.yaml ~/lab/
cp examples/secrets/staging-http.lab.example.yaml ~/.rexecop/secrets.yaml  # edit values
chmod 0600 ~/.rexecop/secrets.yaml
export REXECOP_SECRETS_FILE=~/.rexecop/secrets.yaml
python scripts/run_staging_http_lab.py --env ~/lab/runtime-fixture.staging.example.yaml
```

**CI-equivalent pytest:**

```bash
pytest tests/test_staging_connectors_e2e.py -q
```

- [ ] `staging_http_lab_ok` printed with operation id
- [ ] `validate` → `passed: true`, rule `runtime_fixture.state_observed`
- [ ] No secret material in `"$REXECOP_ROOT"/evidence/` (script checks; `rg` for manual audit)

### 7. Worker and queue smoke

```bash
pytest tests/test_worker_runtime.py -q
# or manual:
rexecop worker run --once
rexecop queue --drain
```

- [ ] Queue drain works without a long-running daemon
- [ ] Scheduling remains **host-owned** (systemd/cron) — see [docs/operator-scheduler-pattern.md](docs/operator-scheduler-pattern.md)

### 8. Alpha sign-off

- [ ] Run `bash scripts/run_alpha_signoff_checks.sh`
- [ ] Complete human checklist in [docs/alpha-sign-off-record.md](docs/alpha-sign-off-record.md)
- [ ] Read [docs/alpha-sign-off.md](docs/alpha-sign-off.md)

## Full E2E lab: profile YAML → GovEngine → SCLite bundle

This walkthrough uses the neutral `http-health-fixture` profile so domain plugins are optional.
It exercises planning, workflow execution, validation, and SCLite bundle emission.

### Step 1 — Prepare environment

Copy the staging template outside git and point connectors at a reachable `/health` endpoint,
or run the pytest E2E which starts an embedded HTTP stub:

```bash
pytest tests/test_http_health_check_e2e.py -q
```

For a manual run, create `~/lab/http-health.env.yaml` with `backend: http_api` and a `health`
connector action (see `examples/environments/` patterns).

### Step 2 — Plan

```bash
export REXECOP_ROOT=~/lab/rexecop-runtime
mkdir -p "$REXECOP_ROOT"

rexecop --root "$REXECOP_ROOT" plan \
  --profile /path/to/RExecOP/examples/profiles/http-health-fixture/profile.yaml \
  --env ~/lab/http-health.env.yaml \
  --intent http_health_check \
  --target local \
  --mode dry_run
```

Record `<operation-id>` from output.

For mutating `apply` plans, verify GovEngine decision events in evidence:

```bash
rg 'govengine_decision' "$REXECOP_ROOT"/evidence/<operation-id>/ || true
```

### Step 3 — Start workflow

```bash
rexecop --root "$REXECOP_ROOT" start --operation <operation-id>
rexecop --root "$REXECOP_ROOT" status --operation <operation-id>
```

Expect terminal state `completed` for the golden path.

### Step 4 — Validate profile rules

```bash
rexecop --root "$REXECOP_ROOT" validate --operation <operation-id>
```

Expect `passed: true` and rule `http_health_check.probe_ok`.

### Step 5 — Inspect SCLite bundle (truth authority)

```bash
ls -la "$REXECOP_ROOT"/sclite/<operation-id>/
```

Expect contract artifacts, scoped ticket, receipt, and evidence sidecars. Receipt
`executed_command_count` should reflect connector steps on staging/http paths.

Compare with non-authoritative export:

```bash
test -f "$REXECOP_ROOT"/receipts/<operation-id>.json && \
  echo "receipt export is summary only — sclite/ is authoritative"
```

### Step 6 — History and redaction

```bash
rexecop --root "$REXECOP_ROOT" history --operation <operation-id>
rg -i 'api_key|token|password' "$REXECOP_ROOT"/evidence/<operation-id>/ || echo "no secret leaks"
```

## GovEngine adapter posture (production vs tests)

| Adapter | Production? | Where used |
| --- | --- | --- |
| `GovEngineClient` | **Yes** — default via `default_govengine_adapter()` | Operator hosts, real governance |
| `StaticGovEngineAdapter` | **No** — bootstrap/tests only | `tests/test_*`, local fixtures |

Rules:

- Do **not** configure `StaticGovEngineAdapter` on operator hosts.
- Pytest and vertical-slice tests may inject the static adapter to avoid external GovEngine
  services — that is not a production governance boundary.
- Mutating `apply` requires a positive GovEngine admission decision **and** satisfied approval
  state; see [docs/govengine-integration.md](docs/govengine-integration.md).

Verify default adapter in code/docs:

```bash
rg 'StaticGovEngineAdapter' tests/ src/rexecop/adapters/govengine_port/
```

Production CLI paths use `default_govengine_adapter()` unless tests inject a substitute.

## Evidence vs SCLite truth

| Location | Role | Authority |
| --- | --- | --- |
| `<root>/evidence/<op>/` | Append-only redacted runtime events (`EvidenceManager`) | Operator telemetry / debugging |
| `<root>/sclite/<op>/` | Full GovEngine-integration bundle (`SCLiteArtifactEmitter`) | **Auditable truth** (SCLite) |
| `<root>/receipts/<op>.json` | Export summary pointing at sclite descriptors | **Not** parallel truth |
| `<root>/operations/`, `plans/` or `rexecop.db` | Runtime operation state (`file` or `sqlite` backend) | RExecOp operator store |
| `<root>/queue/`, `locks/` | Concurrency and run-now backlog | Ephemeral operator mechanics |

Evidence events include `govengine_decision_requested`, `step_completed`, `receipt_generated`.
SCLite owns review semantics (`verify_ticket_use`, review bundles). When both exist, treat
`sclite/` as authoritative for audit — see [docs/evidence-model.md](docs/evidence-model.md)
and [docs/sclite-integration.md](docs/sclite-integration.md).

## Package build smoke

```bash
python -m pip install build twine
rm -rf dist build *.egg-info
python -m build && python -m twine check dist/*
```

CI runs the same checks in the `package-dry-run` job. Details: [docs/distribution.md](docs/distribution.md).

## Related

- [OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/profile-contract.md](docs/profile-contract.md)
- [docs/distribution.md](docs/distribution.md)
