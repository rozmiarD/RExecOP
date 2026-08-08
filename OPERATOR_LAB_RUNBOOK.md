# Operator lab runbook

This runbook validates RExecOp `1.0.0rc2` with public fixtures and optional
staging read-only endpoints. It is not a production mutation procedure.

The lab has two mutation-related purposes only:

1. prove that the stable posture blocks mutating execution; and
2. exercise internal mutation mechanics through tests where all authority and
   connector boundaries are controlled fixtures.

`REXECOP_MUTATION_POSTURE=lab_only` is a development posture. It causes
`doctor` to report a blocker and does not bypass governance, signed authority,
attempt claiming, lease, fencing or capability checks.

## Isolated setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

export REXECOP_ROOT=~/lab/rexecop-runtime
rexecop version
```

Use a new disposable runtime root. Do not point lab commands at a production
runtime root or target catalog. Tecrax is optional and should be installed only
for an explicitly reviewed downstream compatibility exercise.

## Baseline gates

```bash
python scripts/validate_public_truth.py
python scripts/validate_first_run_smoke.py
python scripts/validate_operator_journeys.py
ruff check .
mypy src/rexecop
```

Expected operator-journey result:

```text
operator_journeys_ok:readonly=OK,failure=OK,governance=OK,audit=OK
```

These scripts use sanitized public fixtures. Passing them does not prove that a
real endpoint, external profile, plugin or host adapter is trustworthy.

## Core boundary

```bash
rg -n 'import tecrax|from tecrax' src/rexecop
```

Expected result: no matches. Domain semantics must arrive through profile and
plugin boundaries, not core imports.

## First-run fixture

The `examples/first-run-demo` path requires no domain package, endpoint or
secret:

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

The fixture validates onboarding and planning, not live connector I/O.

## Read-only runtime fixture

The base runtime fixture has no configured policy pack:

```bash
OPERATION=$(
  rexecop --root "$REXECOP_ROOT" plan \
    --profile examples/profiles/runtime-fixture/profile.yaml \
    --env examples/environments/runtime-fixture.example.yaml \
    --intent inspect_fixture_state \
    --target fixture-target \
    --mode dry_run
)

rexecop --root "$REXECOP_ROOT" operation review --operation "$OPERATION"
rexecop --root "$REXECOP_ROOT" start --operation "$OPERATION"
rexecop --root "$REXECOP_ROOT" validate --operation "$OPERATION"
rexecop --root "$REXECOP_ROOT" receipt show "$OPERATION"
```

Expected:

- terminal state `completed`;
- validation `passed: true`;
- a bundle under `<root>/sclite/<operation_id>/`;
- the explicit read-only compatibility binding is not presented as a signed
  GovEngine decision.

To exercise configured read-only policy evaluation, use
`examples/environments/runtime-fixture.policy.example.yaml`:

```bash
pytest -q \
  tests/test_readonly_vertical_slice_e2e.py \
  tests/test_connector_policy_engine.py
```

## HTTP fixture

Domain-neutral in-process test:

```bash
pytest -q tests/test_http_health_check_e2e.py
```

Local staging stub:

```bash
python scripts/run_staging_http_lab.py
```

This starts a loopback fixture and runs the read-only plan, start and validation
path. A real staging endpoint requires an environment file outside Git and
host-owned secret references:

```bash
python scripts/run_staging_http_lab.py \
  --env ~/lab/runtime-fixture.staging.yaml
```

Do not reinterpret a successful loopback or staging result as production
certification.

## Failure and recovery fixture

The operator-journey validator uses
`REXECOP_STATIC_FIXTURE_FAILURES` to inject a bounded transient failure into the
`static_fixture` backend. That environment variable is test-only and must not be
used as an operator control for real connectors.

```bash
python scripts/validate_operator_journeys.py
pytest -q tests/test_runtime_recovery.py
```

Review:

- `ops` and `explain-error` identify the persisted failure;
- retry occurs only when profile policy allows it;
- `runtime recover` reconciles recorded state without inventing an external
  outcome;
- `outcome_indeterminate` is not blindly converted into success or failure.

See [Runtime recovery](docs/runtime-recovery-ops.md).

## Stable mutation-block check

Keep the default posture:

```bash
unset REXECOP_MUTATION_POSTURE

pytest -q \
  tests/test_apply_gating.py \
  tests/test_mutation_posture.py \
  tests/test_m95_execution_permit.py
```

The relevant execution paths must fail with stable reason codes such as
`mutation_not_certified` when mutation is attempted without the required stable
authority and posture.

This runbook intentionally does not provide a copy-and-paste `apply` command.
The stock CLI does not configure production signer, verifier and trust adapters,
and the public RC does not certify live mutation.

## Queue and watchdog fixture

```bash
pytest -q tests/test_worker_runtime.py
rexecop --root "$REXECOP_ROOT" worker run --once
rexecop --root "$REXECOP_ROOT" queue --drain
```

Scheduling remains host-owned. Watchdog and trigger records use RExecOp-owned
contracts; GovEngine owns their planning/supervisor admission, while SCLite
machinery verifies their evidence projections.

## Evidence review

After a completed fixture operation:

```bash
rexecop --root "$REXECOP_ROOT" evidence show "$OPERATION"
rexecop --root "$REXECOP_ROOT" chain explain "$OPERATION"
rexecop --root "$REXECOP_ROOT" support bundle "$OPERATION" --redacted
```

| Location | Role |
| --- | --- |
| `<root>/operations/` or `rexecop.db` | Runtime operation state |
| `<root>/evidence/` | Bounded internal events |
| `<root>/sclite/<operation>/` | SCLite-compatible lifecycle/evidence bundle |
| `<root>/receipts/` | Non-authoritative operator summary |
| `<root>/queue/`, `locks/`, `inbox/` | Runtime coordination |

Redacted projections are not automatically safe to publish. Review exported
files and never include real secret values in a public test report.

## Release qualification

Run the canonical qualification gate when preparing a release:

```bash
bash scripts/run_alpha_signoff_checks.sh
```

The script name is retained for compatibility; it is the current release
qualification gate, not a statement that the complete package is alpha.

Complete:

- [Release qualification](docs/release-qualification.md)
- [Release qualification record](docs/release-qualification-record.md)
- [Known limitations](docs/known-limitations.md)

## Package smoke

Build into a clean output directory or clean worktree:

```bash
python -m build
python -m twine check dist/*
python scripts/validate_artifact_install_smoke.py
```

See [Distribution](docs/distribution.md) for the complete release procedure.

## Checklist

- [ ] Public truth, first-run and operator-journey gates pass.
- [ ] No domain-package imports exist in core.
- [ ] Read-only fixture completes and evidence projections verify.
- [ ] Configured policy fixture exercises GovEngine evaluation.
- [ ] Stable mutation-block tests pass.
- [ ] Failure/recovery fixture preserves uncertainty honestly.
- [ ] Runtime root contains no deliberately supplied real credentials.
- [ ] Release-candidate claims and non-claims are accepted.

## Related

- [Operator runbook](OPERATOR_RUNBOOK.md)
- [Architecture](docs/architecture.md)
- [Safety model](docs/safety-model.md)
- [SCLite integration](docs/sclite-integration.md)
- [Distribution](docs/distribution.md)
