# RExecOp

[![CI: pytest](https://github.com/rozmiarD/RExecOP/actions/workflows/ci.yml/badge.svg)](https://github.com/rozmiarD/RExecOP/actions/workflows/ci.yml)
[![Package: rexecop 0.2.18a0](https://img.shields.io/badge/package-rexecop%200.2.18a0-blueviolet.svg)](https://pypi.org/project/rexecop/0.2.18a0/)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Dependency: GovEngine](https://img.shields.io/badge/dependency-GovEngine-informational.svg)](https://github.com/rozmiarD/GovEngine)
[![Dependency: SCLite](https://img.shields.io/badge/dependency-SCLite-informational.svg)](https://github.com/rozmiarD/SCLite)
[![Profile: tecrax](https://img.shields.io/badge/profile-tecrax-informational.svg)](https://github.com/rozmiarD/tecrax)
[![Status: alpha](https://img.shields.io/badge/status-alpha-green.svg)](#status)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

**Regulated Execution Operations** control-plane for profile-defined workflows, bound to
**GovEngine** governance and **SCLite** auditable truth.

RExecOp (package name: `rexecop`) is the deterministic **runner, orchestrator, and executor**
for domain profiles. It plans and runs declared workflow steps, enforces operational lifecycle
mechanics, and projects completed work into SCLite-compatible artifacts — without becoming a
policy engine or a parallel truth layer.

## Status

| Item | Value |
| --- | --- |
| Current source line | `0.2.18a0` |
| Maturity | **alpha** — operator evaluation with documented limits |
| Delivery | Published single supported alpha line; older PyPI releases are archived only |
| Tests | CI reruns the current suite; `pytest -m delivery` runs the sign-off scope |
| Latest PyPI | [`rexecop==0.2.18a0`](https://pypi.org/project/rexecop/0.2.18a0/) |
| Source dependencies | `govengine==0.16.9`, `sclite-core==1.0.8` (see `pyproject.toml`) |
| Stack compatibility | [`docs/stack-contract-compatibility.md`](docs/stack-contract-compatibility.md) |
| Default posture | `dry_run` / read-only first; `apply` requires GovEngine allow |

## Project sentence

> RExecOp runs profile-defined operations under GovEngine admission and records auditable outcomes through SCLite — profiles own meaning, GovEngine owns governance, SCLite owns proof, RExecOp owns execution mechanics.

## Stack position

One operation crosses all layers. GovEngine owns policy and admission decisions;
RExecOp enforces admitted neutral controls and executes the workflow; SCLite
validates the **proof bundle** emitted after execution.

```text
Profiles (tecrax, fixtures)
  intents, workflows, connector contracts, validation rules
        |
        v
RExecOp  plan -> GovEngine policy/admission -> lifecycle FSM
        |                  allowed | blocked | approval_required
        v
RExecOp  admitted controls -> step execution -> profile validation
        v
RExecOp  project runtime facts + GovEngine admission into SCLite artifact shapes
        |
        v
SCLite   validate schemas, ticket binding, review_bundle (truth authority)
```

| Layer | Responsibility |
| --- | --- |
| **Profiles** | Intents, workflows, connector contracts, declarative validation rules |
| **RExecOp** | Runner: lifecycle, planning, step dispatch, pause/resume/retry, queue/lock; **projects** completed operations into SCLite bundles (does not decide policy) |
| **GovEngine** | Governance: admission and runner request/receipt **contracts** — does not execute steps or emit SCLite files |
| **SCLite** | Proof: auditable artifacts, scoped tickets, receipt-bounded evidence, review bundles |

Tecrax ships as the [`tecrax`](https://github.com/rozmiarD/tecrax) package (`rexecop.profiles:tecrax`).
Ravenclaw is legacy and out of scope for RExecOp.

## What RExecOp includes now

**Core execution**

- Deterministic operation state machine and `OperationPlan` runtime artifact
- GovEngine port: real `GovEngineClient` + bootstrap-only `StaticGovEngineAdapter`
- SCLite port: GovEngine-integration bundle emission (scoped ticket, kernel guard, review pass)
- Profile resolution by path or `rexecop.profiles` entry point (`tecrax`)
- Declarative profile validation rules (YAML, not hardcoded domain logic in core)
- Deterministic reaction interpreter (`reaction-plan`, `reaction-start`, `reaction-replay`)
- Connectors: `mock`, `http_api`, `local_shell_readonly`, `ssh_readonly` (bounded output + digests)
- Execution contracts: digest-bound `ExecutionRequest` / `ExecutionReceipt` (schema `v0.2`)
- GovEngine `PolicyEngine` when `environment.policy_pack` is set
- Operator target catalog and profile-derived operation catalog with drift rejection at start
- Storage: `FileStore` (default) or optional `SqliteStore` (`REXECOP_STORAGE` / `--storage`)
- Secrets port: `REXECOP_SECRET_*` and `REXECOP_SECRETS_FILE` (no plaintext secrets in git or `.rexecop/`)

**Runtime readiness and operator UX**

- Runtime root: `--root` / `REXECOP_ROOT`, `--instance` / `REXECOP_INSTANCE`, `init`, `doctor`
- Input validation: `env lint`, `profile lint`, `secrets doctor`, `secrets suggest-ref`
- Profile developer surface: `profiles list/show`, `connectors list/show`, `capabilities list`,
  `profile manifest`, `profile harness`, operator metadata projection
- CLI contract registry: `contracts cli` emits command schemas, formats, exit-code policy
  and redaction/authority claims for operator-facing surfaces, including
  command groups, `format_matrix` and `exit_code_matrix`
- CLI error envelope: registry commands emit `rexecop.cli_error.v0.1` on exit
  code `1` with normalized class, reason code, redacted message and safe next actions
- Observability: bounded structured logs with correlation IDs and artifact refs;
  `observability diagnostics` uses the same failure classes as `explain-error`
- M5 action metadata (no backend IO): `action list`, `action show`, `action preview`,
  `action policy-preview`, `action validate`, `action diff --env`,
  `action configure --dry-run`, `action templates list` (scope 1.0: `http.simple-get`,
  shell/SSH allowlist skeletons)
- Pre-run inspection: `policy explain`, `operations explain`, `operation explain`,
  `operation review`, `operation diff`, `runbook show`, `operations unavailable`
- Audit inspection: `receipt show`, `evidence show`, `chain summary`,
  `support bundle --redacted` for redacted, digest-bound runtime/SCLite projections
- Runtime triage: `runtime status`, `ops`, `explain-error`, `dead-letter list/show`,
  `locks list`, `runtime recover`, `backup create/restore`, `watchdog manual-record`
- Lifecycle controls: `plan`, `approve`, `start`, `pause`/`resume`, `cancel`, `retry`,
  `rollback`, `validate`, `escalate`, `status`, `history`
- Host-owned scheduling: `queue`, `worker run`, `trigger` (see operator scheduler pattern)

**Examples and fixtures**

- Domain-neutral `examples/first-run-demo/` and `examples/profiles/runtime-fixture/` for
  onboarding, lifecycle, policy and connector regressions
- Tecrax product semantics live only in the external `tecrax` package

## What RExecOp does not include

- A policy engine (GovEngine is the governance authority)
- SCLite schema authority or long-term truth storage
- Domain profiles in core (no Tecrax/Ravenclaw operational logic in `src/rexecop`)
- Production cron/recurrence scheduler (host-owned worker + systemd/cron pattern only)
- Web UI or multi-tenant RBAC
- Unattended apply on critical infrastructure without operator and governance gates
- `mutation_ready` apply on production targets without explicit stack gate update

## Installation

Published alpha package:

```bash
python -m pip install "rexecop==0.2.18a0"
rexecop version
```

The published `0.2.18a0` wheel is the single supported alpha stack line for readonly
evaluation, M2–M8 operator UX (CLI contracts, error envelope, observability,
explain/review/diff, triage/recovery, profile developer surface, action metadata),
catalog drift binding, watchdog decision truth and manual recovery record paths.
Older PyPI lines do not contain the watchdog decision truth path or manual recovery
record path guarantees bundled in `0.2.18a0`.

See [docs/distribution.md](docs/distribution.md) for Tecrax extra, wheels, Git URL, and private index notes.

From source (development):

```bash
git clone https://github.com/rozmiarD/RExecOP.git
cd RExecOP
python -m venv .venv && source .venv/bin/activate
git clone https://github.com/rozmiarD/GovEngine.git ../govengine
pip install -e ../govengine
pip install -e ".[dev]"
```

With the Tecrax profile package:

```bash
pip install "rexecop[tecrax]==0.2.18a0"
# or, for coordinated development: pip install -e /path/to/tecrax
```

CI also checks out [`tecrax`](https://github.com/rozmiarD/tecrax) for integration tests.

## Quick start

```bash
rexecop version

rexecop --root /tmp/rexecop-first-run init --guided

rexecop --root /tmp/rexecop-first-run doctor \
  --profile examples/first-run-demo/profile/profile.yaml \
  --env examples/first-run-demo/environment.yaml \
  --catalog examples/first-run-demo/catalog.yaml

rexecop operations explain inspect \
  --profile examples/first-run-demo/profile/profile.yaml

rexecop --root /tmp/rexecop-first-run plan \
  --catalog examples/first-run-demo/catalog.yaml \
  --intent inspect \
  --target fixture-target \
  --mode dry_run
```

See [docs/first-run.md](docs/first-run.md) for the full no-I/O first-run path
with lint checks.

- With `tecrax` installed, `--profile tecrax` resolves via entry point.
- For offline tests without a domain package, use `examples/profiles/runtime-fixture/profile.yaml`.
- Staging `http_api` template: `examples/environments/runtime-fixture.staging.example.yaml`

Runtime artifacts live under the selected runtime root: operations, evidence,
SCLite bundles, receipt exports, queue, locks and trigger inbox.

## CLI overview

The CLI has grown across M1–M5 milestones. **Full command reference:**
[docs/cli-reference.md](docs/cli-reference.md).

| Group | Commands |
| --- | --- |
| Runtime readiness | `init`, `doctor`, `env lint`, `version` |
| Secrets | `secrets doctor`, `secrets suggest-ref` |
| Profile developer | `profile lint`, `profile manifest`, `profile harness`, `profiles list/show`, `connectors list/show`, `capabilities list` |
| Action metadata | `action list`, `action show`, `action preview`, `action policy-preview`, `action validate`, `action diff`, `action configure` |
| Catalog | `targets list/show`, `operations list`, `operations explain`, `operations unavailable` |
| Pre-run inspection | `policy explain`, `operation explain`, `operation review`, `operation diff`, `runbook show` |
| Runtime triage | `runtime status`, `ops`, `explain-error`, `dead-letter list/show`, `locks list`, `runtime recover`, `backup create/restore`, `watchdog manual-record` |
| Observability | `observability logs list`, `observability diagnostics` |
| Lifecycle | `plan`, `approve`, `start`, `pause`, `resume`, `cancel`, `retry`, `rollback`, `validate`, `escalate`, `status`, `history` |
| Scheduling | `queue`, `worker run`, `trigger` |
| Reactions | `reaction-plan`, `reaction-start`, `reaction-replay`, `reaction-proposal-validate` |

Global options: `--root`, `--instance`, `--storage file|sqlite`.

## Development

```bash
pip install -e /path/to/tecrax -e ".[dev]"
python scripts/validate_public_truth.py
python scripts/validate_first_run_smoke.py
ruff check .
mypy src/rexecop
python -m build && python -m twine check dist/*
pytest
pytest -m delivery   # canonical sign-off scope from tests/delivery_scope.py
```

GitHub Actions runs on every push and pull request: install `tecrax`, public truth validation,
stack contract validation, profile conformance, first-run smoke, ruff, mypy, core boundary
grep, secret scan, pytest, and a `package-dry-run` job (`build` + `twine check`).

## Documentation

| Document | Topic |
| --- | --- |
| [docs/cli-reference.md](docs/cli-reference.md) | Complete CLI command reference |
| [docs/first-run.md](docs/first-run.md) | No-I/O onboarding: init, doctor, lint, plan |
| [docs/operation-lifecycle.md](docs/operation-lifecycle.md) | States, lifecycle orchestration, queue/lock |
| [docs/runtime-recovery-ops.md](docs/runtime-recovery-ops.md) | Triage, explain-error, recovery, backup and watchdog manual-record |
| [docs/profile-developer-surface.md](docs/profile-developer-surface.md) | Profiles/connectors/capabilities discoverability and extension manifest |
| [docs/secrets-operator.md](docs/secrets-operator.md) | `secrets doctor`, ref resolution and file policy |
| [docs/reaction-interpreter.md](docs/reaction-interpreter.md) | Deterministic reaction DSL and CLI |
| [docs/architecture.md](docs/architecture.md) | Layer boundaries and execution path |
| [docs/stack-contract-compatibility.md](docs/stack-contract-compatibility.md) | Cross-repo contract matrix and readiness labels |
| [docs/operator-scheduler-pattern.md](docs/operator-scheduler-pattern.md) | Host-owned scheduling with worker/systemd |
| [docs/govengine-integration.md](docs/govengine-integration.md) | Governance port and apply gating |
| [docs/sclite-integration.md](docs/sclite-integration.md) | Artifact emission and authority model |
| [docs/evidence-model.md](docs/evidence-model.md) | Internal events vs SCLite truth |
| [docs/profile-contract.md](docs/profile-contract.md) | Profile layout and entry points |
| [docs/connector-contract.md](docs/connector-contract.md) | `http_api`, secrets, error taxonomy |
| [docs/execution-contract.md](docs/execution-contract.md) | ExecutionRequest/Receipt, bounded output |
| [docs/environment-contract.md](docs/environment-contract.md) | Target, group, and connector semantics |
| [docs/operator-catalog.md](docs/operator-catalog.md) | Target catalog, operation projection, applicability and drift binding |
| [docs/storage-backends.md](docs/storage-backends.md) | File vs SQLite boundaries |
| [docs/safety-model.md](docs/safety-model.md) | Hard safety rules and operator posture |
| [docs/known-limitations.md](docs/known-limitations.md) | Alpha scope and explicit non-claims |
| [docs/distribution.md](docs/distribution.md) | Wheels, Git install, private index |
| [docs/alpha-sign-off.md](docs/alpha-sign-off.md) | Automated and human sign-off gates |
| [docs/adr-001-http-action-identity.md](docs/adr-001-http-action-identity.md) | HTTP action identity ADR |
| [OPERATOR_LAB_RUNBOOK.md](OPERATOR_LAB_RUNBOOK.md) | Lab checklist and E2E walkthrough |
| [OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md) | Installation, secrets, workflows, troubleshooting |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## Related repositories

| Repository | Role |
| --- | --- |
| [GovEngine](https://github.com/rozmiarD/GovEngine) | Governance kernel and admission contracts |
| [SCLite](https://github.com/rozmiarD/SCLite) | Auditable contract lifecycle and review bundles |
| [tecrax](https://github.com/rozmiarD/tecrax) | Tecrax domain profile and local-fixture package |

## License

MIT — see [LICENSE](LICENSE).
