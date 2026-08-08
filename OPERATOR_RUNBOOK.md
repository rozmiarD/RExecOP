# Operator runbook

This runbook covers the stable read-only posture of RExecOp `1.0.0rc2`.
Mutating execution is intentionally excluded: the stock `stable_read_only`
posture rejects `apply` and `recovery` before execution and again before
connector I/O.

Use the [lab runbook](OPERATOR_LAB_RUNBOOK.md) only for fixture-based mechanics
and expected mutation-block checks.

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Python | 3.11 or newer |
| RExecOp | Exact `rexecop==1.0.0rc2` release candidate |
| Dependencies | Exact `govengine==1.0.0rc2` and `sclite-core==2.0.1` pins are installed with RExecOp |
| Profile | A reviewed profile path or separately installed profile package |
| Operator host | Access only to the targets and secret provider required for the selected read-only workflow |

Tecrax is an optional external profile package. It is not part of the RExecOp
distribution.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install "rexecop==1.0.0rc2"
rexecop version
```

Use a source checkout only for development:

```bash
python -m pip install -e ".[dev]"
python scripts/validate_public_truth.py
python scripts/validate_first_run_smoke.py
python scripts/validate_operator_journeys.py
```

See [Distribution](docs/distribution.md) for build, index and source-install
guidance.

## Runtime root

Always select an explicit root for operator work:

```bash
export REXECOP_ROOT=~/rexecop-runtime
rexecop init --guided
```

`--root` overrides the environment. Named `--instance` values resolve under
`./.rexecop/instances/` unless an explicit root is supplied.

Treat the complete runtime root as sensitive. Redaction reduces exposure but
does not make every persisted file safe to publish.

## Readiness checks

Run these checks before using a new profile or environment:

```bash
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

For a no-I/O walkthrough, follow [First run](docs/first-run.md).

`doctor` reports runtime configuration and `security_blockers`; it is not a
vulnerability scan or a certification of the selected targets, connectors or
host adapters.

## Secrets

Secret values remain host-owned. Environment and profile documents must contain
references, not inline values.

Example host file outside the repository:

```yaml
secrets:
  fixture_base_url: https://api-staging.example.invalid
  fixture_api_token: REPLACE_ME
  fixture_ca_file: /path/outside/repo/ca.pem
```

```bash
chmod 0600 ~/.rexecop/secrets.yaml
export REXECOP_SECRETS_FILE=~/.rexecop/secrets.yaml

rexecop secrets doctor \
  --env ~/.rexecop/environment.yaml \
  --secrets-file ~/.rexecop/secrets.yaml
```

Environment YAML uses `secret_ref` or `base_url_secret_ref`. The operator is
responsible for file permissions, process environment exposure, provider audit
and rotation. See [Secrets](docs/secrets-operator.md).

## Standard read-only workflow

### 1. Inspect configuration

```bash
rexecop env lint --env <environment.yaml> --profile <profile.yaml>
rexecop profile lint --profile <profile.yaml> --track readonly
rexecop secrets doctor --env <environment.yaml>
rexecop operations unavailable --profile <profile.yaml> --env <environment.yaml>
rexecop action preview <intent> \
  --profile <profile.yaml> \
  --env <environment.yaml> \
  --target <target>
```

`action preview` is a metadata projection. It does not invoke a backend.

### 2. Plan and review

```bash
OPERATION=$(
  rexecop --root "$REXECOP_ROOT" plan \
    --profile <profile.yaml> \
    --env <environment.yaml> \
    --intent <intent> \
    --target <target> \
    --mode dry_run
)

rexecop --root "$REXECOP_ROOT" operation review --operation "$OPERATION"
rexecop --root "$REXECOP_ROOT" operation diff --operation "$OPERATION"
rexecop governance controls --profile <profile.yaml> --track readonly
```

`governance controls` projects the supported GovEngine control catalog. It is
not admission for this operation. A read-only operation without a configured
policy pack may use the explicit `legacy_read_only` binding and must not be
described as carrying signed governance authenticity.

### 3. Start and inspect

```bash
rexecop --root "$REXECOP_ROOT" start --operation "$OPERATION"
rexecop --root "$REXECOP_ROOT" status --operation "$OPERATION"
rexecop --root "$REXECOP_ROOT" validate --operation "$OPERATION"
rexecop --root "$REXECOP_ROOT" history --operation "$OPERATION"
```

Inspect bounded projections:

```bash
rexecop --root "$REXECOP_ROOT" receipt show "$OPERATION"
rexecop --root "$REXECOP_ROOT" evidence show "$OPERATION"
rexecop --root "$REXECOP_ROOT" operation truth-path --operation "$OPERATION"
rexecop --root "$REXECOP_ROOT" chain summary "$OPERATION"
rexecop --root "$REXECOP_ROOT" chain explain "$OPERATION"
rexecop --root "$REXECOP_ROOT" support bundle "$OPERATION" --redacted
```

These commands are operator projections over persisted state. They do not
create a second truth store, and their output still requires review before
external sharing.

## Triage and recovery

When an operation or worker is unhealthy:

```bash
rexecop --root "$REXECOP_ROOT" ops
rexecop --root "$REXECOP_ROOT" runtime status --json
rexecop --root "$REXECOP_ROOT" runtime reconstruct-status --json
rexecop explain-error <operation-id-or-record-ref>
rexecop runbook show <intent> --profile <profile.yaml>
rexecop dead-letter list
rexecop locks list
```

For a retry explicitly allowed by the profile:

```bash
rexecop --root "$REXECOP_ROOT" retry --operation <operation-id>
```

After a host restart or worker interruption:

```bash
rexecop --root "$REXECOP_ROOT" runtime recover --json
```

`runtime recover` reconciles persisted runtime mechanics. It does not authorize
mutation, invent an external result or resolve every `outcome_indeterminate`
attempt automatically.

Before maintenance:

```bash
rexecop --root "$REXECOP_ROOT" backup create \
  --output ~/backups/rexecop-runtime.tar.gz
```

See [Runtime recovery](docs/runtime-recovery-ops.md).

## Queue, worker and triggers

```bash
rexecop --root "$REXECOP_ROOT" queue
rexecop --root "$REXECOP_ROOT" queue --drain
rexecop --root "$REXECOP_ROOT" worker run --once
```

Host schedulers invoke RExecOp; RExecOp does not ship a recurrence service. A
scheduler does not bypass runtime posture or governance.

See [Host-owned scheduling](docs/operator-scheduler-pattern.md).

## Runtime storage

`FileStore` is the stable single-host default. `SqliteStore` remains an alpha
backend selected through `REXECOP_STORAGE=sqlite` or `--storage sqlite`.
Neither backend is multi-tenant or highly available.

| Path | Role |
| --- | --- |
| `operations/` | FileStore operations and plans |
| `rexecop.db` | SQLite operations, plans and evidence |
| `evidence/` | Internal bounded events |
| `sclite/<operation>/` | SCLite-compatible lifecycle/evidence bundle |
| `receipts/` | Non-authoritative summary exports |
| `queue/`, `locks/`, `inbox/` | Runtime coordination |

Queue, locks, bundles and receipts remain filesystem state for both storage
backends. Back up the runtime root according to the host retention policy.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `profile not found` | Install the external profile package or supply a profile path |
| `internal_action_not_registered` | Install the package that owns the declared action |
| `secret not found` | Check `REXECOP_SECRETS_FILE`, provider mapping and file permissions |
| `capability_undeclared` | Compare the action with the profile connector declaration |
| `connector disabled` | Inspect the selected environment |
| `mutation_not_certified` | Expected under `stable_read_only`; do not bypass it in this runbook |
| Other mutating execution block | Inspect posture, signed authority, decision/permit binding, approval, inventory, lease/fencing and maintenance constraints |
| Queue appears stuck | Run `ops`, `queue`, `runtime status` and `locks list` before recovery |
| `outcome_indeterminate` | Do not blindly retry; follow the recovery document and inspect external state through an authorized read-only path |
| Wrong runtime path | Run `doctor` and use an explicit `--root` |

## Operator checklist

- [ ] Exact package and dependency versions verified.
- [ ] Explicit runtime root selected and protected.
- [ ] Profile, environment and catalog reviewed.
- [ ] Secret references resolve without printing secret values.
- [ ] `doctor`, lint and applicable conformance checks pass.
- [ ] Read-only connector capabilities and target scope reviewed.
- [ ] Operation reviewed before start.
- [ ] Receipt, evidence and truth-path projections inspected after execution.
- [ ] `outcome_indeterminate` handled without assuming exactly-once effects.
- [ ] Release-candidate limitations accepted for the intended use.

## Related documents

- [Profile developer surface](docs/profile-developer-surface.md)
- [Secrets](docs/secrets-operator.md)
- [Architecture](docs/architecture.md)
- [Safety model](docs/safety-model.md)
- [GovEngine integration](docs/govengine-integration.md)
- [SCLite integration](docs/sclite-integration.md)
- [Known limitations](docs/known-limitations.md)
- [CHANGELOG](CHANGELOG.md)
