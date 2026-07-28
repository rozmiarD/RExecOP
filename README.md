# RExecOp

[![CI: pytest](https://github.com/rozmiarD/RExecOP/actions/workflows/ci.yml/badge.svg)](https://github.com/rozmiarD/RExecOP/actions/workflows/ci.yml)
[![Package: rexecop 1.0.0rc1](https://img.shields.io/badge/package-rexecop%201.0.0rc1-blueviolet.svg)](https://pypi.org/project/rexecop/1.0.0rc1/)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Status: release candidate](https://img.shields.io/badge/status-release%20candidate-green.svg)](#release-status)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

RExecOp is a domain-neutral execution kernel and runtime for controlled,
auditable operations.

It turns an accepted operation into bounded execution attempts: it coordinates
operation state, queues, leases, fencing, retries and recovery, dispatches
connectors, and produces evidence describing what was attempted and observed.

RExecOp does not decide what an operation means or whether an organization
should allow it. Domain semantics belong to profiles, governance decisions
belong to [GovEngine](https://github.com/rozmiarD/GovEngine), and canonical
evidence contracts and verification belong to
[SCLite](https://github.com/rozmiarD/SCLite). RExecOp owns the runtime mechanics
that connect those inputs and decisions to actual I/O.

## Release status

| Item | Current value |
| --- | --- |
| Package | [`rexecop==1.0.0rc1`](https://pypi.org/project/rexecop/1.0.0rc1/) |
| Maturity | Release candidate with a stable read-only core |
| Python | 3.11 or newer |
| Exact dependencies | `govengine==1.0.0rc1`, `sclite-core==2.0.0` |
| Default posture | `stable_read_only` |
| Mutating execution | Blocked by the stock stable posture |
| Compatibility | [Stack contract compatibility](docs/stack-contract-compatibility.md) |

The `main` branch may contain changes recorded under
[Unreleased](CHANGELOG.md#unreleased). The public `1.0.0rc1` wheel is the
current evaluation and integration release.

## Why RExecOp exists

Calling a connector is easy. Safely coordinating an operation around that call
is harder.

Before and after I/O, a runtime may need to claim work atomically, bind it to
the current executor, persist an attempt, enforce governance controls, recover
after a crash, and produce evidence without intentionally persisting secret
material or unbounded output. RExecOp provides those runtime mechanics without
embedding the semantics of a particular business or infrastructure domain.

## Where it fits

```text
Domain profile
  intent, targets, workflow and validation semantics
        |
        v
RExecOp planning
  validate inputs and build the operation plan
        |
        v
GovEngine decision where required
  policy, governance, admission and execution authorization
        |
        v
RExecOp execution
  lifecycle, queue/lease/fencing, connector I/O and recovery
        |
        v
SCLite verification
  canonical evidence contracts, integrity and verification
```

These are ownership boundaries, not a claim that every compatibility path uses
every component identically. Mutating operations require GovEngine governance.
Read-only operations can evaluate configured policy, but the explicit
`legacy_read_only` compatibility path does not carry a signed per-attempt
GovEngine decision and must not be presented as governance authenticity.

RExecOp owns the orchestration-specific observation, finding, reaction plan,
escalation proposal, trigger decision, watchdog decision and automation-chain
contracts. SCLite provides the canonicalization and verification machinery used
for their evidence projections; it does not own their runtime semantics.

Tecrax is a separate domain-profile package and downstream consumer. It is not
part of the RExecOp distribution or release train. Ravenclaw is legacy and out
of scope.

See [Architecture](docs/architecture.md) for the complete ownership model.

## What RExecOp claims

Within its documented contracts and supported configuration, RExecOp provides:

- deterministic orchestration decisions for equivalent recorded inputs and
  state;
- durable operation and attempt records around connector execution;
- atomic queue claims, leases and fencing against stale executors;
- bounded retry, rollback and recovery transitions;
- explicit `outcome_indeterminate` handling for the post-I/O,
  pre-durable-result uncertainty window;
- pre-I/O governance enforcement for mutating execution;
- connector dispatch through declared runtime capabilities;
- bounded, redacted audit projections for supported execution paths;
- versioned stable and alpha public-interface classifications;
- exact compatibility pins for the supported GovEngine and SCLite line.

External I/O itself is not deterministic. The deterministic claim applies to
RExecOp decisions over equivalent recorded inputs and state.

## What RExecOp does not claim

RExecOp does not claim that:

- arbitrary operations, profiles, plugins or connectors are safe;
- external side effects are exactly-once;
- a successful connector call proves the intended real-world outcome;
- recovery can always determine whether an interrupted side effect occurred;
- read-only compatibility mode carries signed governance authenticity;
- the stock `1.0.0rc1` CLI supports unrestricted production mutation;
- redaction makes every runtime artifact safe to publish without operator
  review;
- RExecOp is a policy engine, secret manager, domain workflow product,
  long-running scheduler service or truth database;
- RExecOp replaces GovEngine or SCLite;
- installing the package supplies trustworthy signer, verifier, approval,
  storage or connector adapters for a particular production environment.

The current security posture and residual risks are documented in
[Safety model](docs/safety-model.md),
[Known limitations](docs/known-limitations.md), and
[Security threat model](docs/security-threat-model.md).

## What ships in `1.0.0rc1`

### Operation runtime

- operation planning and lifecycle state;
- atomic FIFO queue claims and one fenced executor per `FileStore` root;
- durable connector attempts, retry, rollback and recovery;
- host-driven worker, trigger, reaction and watchdog mechanics.

### Execution safety

- `stable_read_only` as the default mutation posture;
- pre-I/O mutation posture and permit checks;
- attempt, lease, fencing, runtime-instance and capability-inventory bindings;
- bounded connector output, receipt bindings and explicit uncertainty states.

### Connectors and profiles

- profile resolution by path or `rexecop.profiles` entry point;
- declarative workflow, environment, target and capability validation;
- `mock`, `http_api`, `local_shell_readonly` and `ssh_readonly` connector
  implementations;
- separate external domain packages such as Tecrax.

### Evidence and operator inspection

- SCLite-compatible operation bundles and receipt projections;
- `operation review`, `operation diff`, `receipt show`, `evidence show`,
  `chain summary`, `chain explain`, `reaction explain` and
  `support bundle --redacted`;
- structured logs, `observability diagnostics`, `runtime status`,
  `explain-error`, `dead-letter list`, `locks list` and `runbook show`;
- stable `rexecop.cli_error.v0.1` error envelopes.

### Interface contracts

- the `rexecop.public_api.v1` Python-import manifest;
- a machine-readable CLI registry from `contracts cli`;
- `format_matrix`, `exit_code_matrix` and stable/alpha command
  classifications;
- profile developer commands including `secrets doctor`, `secrets suggest-ref`,
  `profiles list`, `profile manifest`, `profile harness`, `connectors list`,
  `capabilities list`, `action list`, `action show`, `action preview`,
  `action configure`, `action diff`, `action templates`,
  `action policy-preview`, `action validate`, `operations unavailable`,
  `runtime recover`, `backup create` and `watchdog manual-record`.

The built-in read-only action templates include `http.simple-get` and bounded
shell/SSH allowlist skeletons. Templates describe configuration shapes; they do
not execute backend I/O.

The exhaustive command and schema inventories live in
[CLI reference](docs/cli-reference.md) and
[Public API](docs/public-api.md), not in this overview.

## Install

Install the public release candidate:

```bash
python -m pip install "rexecop==1.0.0rc1"
rexecop version
```

For a source checkout used for development:

```bash
git clone https://github.com/rozmiarD/RExecOP.git
cd RExecOP
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

See [Distribution](docs/distribution.md) for wheel, source, private-index and
release-verification guidance.

## Read-only quick start

The bundled first-run fixture plans a no-I/O operation. Materialize it into a
new local directory first; the command refuses existing directories and never
creates a runtime root or executes a workflow:

```bash
rexecop version

rexecop examples materialize --output /tmp/rexecop-first-run-demo

rexecop --root /tmp/rexecop-first-run init --guided

rexecop --root /tmp/rexecop-first-run doctor \
  --profile /tmp/rexecop-first-run-demo/profile/profile.yaml \
  --env /tmp/rexecop-first-run-demo/environment.yaml \
  --catalog /tmp/rexecop-first-run-demo/catalog.yaml

rexecop operations explain inspect \
  --profile /tmp/rexecop-first-run-demo/profile/profile.yaml

rexecop --root /tmp/rexecop-first-run plan \
  --catalog /tmp/rexecop-first-run-demo/catalog.yaml \
  --intent inspect \
  --target fixture-target \
  --mode dry_run
```

Continue with [First run](docs/first-run.md). Runtime state belongs under the
selected `--root`; treat that directory as sensitive even when using redacted
inspection commands.

No mutating quick start is provided. The stock stable posture intentionally
blocks mutating execution.

## Execution model

```text
accept -> plan -> govern when required -> claim and validate permit
       -> persist attempt -> revalidate pre-I/O controls -> perform I/O
       -> persist outcome
       -> project evidence -> terminal state or recovery
```

A connector may complete externally before its result becomes durable. RExecOp
records that uncertainty as `outcome_indeterminate`; it does not invent an
exactly-once guarantee.

## Public interfaces

`rexecop.public_api.public_api_manifest()` is the machine-readable source of
truth for supported Python imports and CLI stability. The 1.x compatibility
promise is deliberately smaller than the installed package:

- stable commands and imports carry the documented 1.x compatibility policy;
- alpha commands do not carry a 1.x output compatibility promise;
- alpha runtime roots require a new 1.x root instead of an in-place migration.

Use `rexecop contracts cli` for the command registry. See
[Public API](docs/public-api.md) for the exact surface.

## Documentation

### Start here

- [First run](docs/first-run.md) — no-I/O onboarding.
- [CLI reference](docs/cli-reference.md) — command contracts and stability.
- [Architecture](docs/architecture.md) — components and ownership boundaries.

### Operate

- [Operator runbook](OPERATOR_RUNBOOK.md) — stable read-only operation.
- [Lab runbook](OPERATOR_LAB_RUNBOOK.md) — fixture-only mechanics and blocked
  mutation checks.
- [Runtime recovery](docs/runtime-recovery-ops.md) — triage, uncertainty,
  backup and recovery.
- [Storage backends](docs/storage-backends.md) and
  [Secrets](docs/secrets-operator.md).

### Integrate and extend

- [Public API](docs/public-api.md).
- [Profile contract](docs/profile-contract.md) and
  [profile developer surface](docs/profile-developer-surface.md).
- [Execution contract](docs/execution-contract.md),
  [connector contract](docs/connector-contract.md), and
  [environment contract](docs/environment-contract.md).
- [GovEngine integration](docs/govengine-integration.md) and
  [SCLite integration](docs/sclite-integration.md).
- [Scheduler pattern](docs/operator-scheduler-pattern.md) and
  [reaction interpreter](docs/reaction-interpreter.md).

### Review safety and releases

- [Safety model](docs/safety-model.md),
  [known limitations](docs/known-limitations.md), and
  [security threat model](docs/security-threat-model.md).
- [Stack contract compatibility](docs/stack-contract-compatibility.md).
- [Release qualification](docs/release-qualification.md).
- [Release evidence](docs/release-evidence/README.md) and
  [security review](docs/release-security-review/README.md).
- [Distribution](docs/distribution.md) and [CHANGELOG](CHANGELOG.md).

## Development

```bash
python scripts/validate_public_truth.py
python scripts/validate_first_run_smoke.py
python scripts/validate_operator_journeys.py
ruff check .
mypy src/rexecop
pytest
```

The release qualification procedure adds artifact-install, clean-install and
supply-chain checks. A separate live GitHub protection check is required before
publication.

## Security

Report vulnerabilities through the process in [SECURITY.md](SECURITY.md). Do
not include credentials, secret values, private connector output or sensitive
runtime artifacts in a public issue.

## License

MIT — see [LICENSE](LICENSE).
