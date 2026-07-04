# RExecOp

[![CI: pytest](https://github.com/rozmiarD/RExecOP/actions/workflows/ci.yml/badge.svg)](https://github.com/rozmiarD/RExecOP/actions/workflows/ci.yml)
[![Package: rexecop 0.2.11a0](https://img.shields.io/badge/package-rexecop%200.2.11a0-blueviolet.svg)](https://pypi.org/project/rexecop/0.2.11a0/)
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
| Current source line | `0.2.11a0` |
| Maturity | **alpha** — operator evaluation with documented limits |
| Delivery | Published single supported alpha line; older PyPI releases are archived only |
| Tests | CI reruns the current suite |
| Latest PyPI | [`rexecop==0.2.11a0`](https://pypi.org/project/rexecop/0.2.11a0/) |
| Source dependencies | `govengine==0.16.6`, `sclite-core==1.0.8` (see `pyproject.toml`) |
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

- Deterministic operation state machine and `OperationPlan` runtime artifact
- GovEngine port: real `GovEngineClient` + bootstrap-only `StaticGovEngineAdapter`
- SCLite port: full GovEngine-integration bundle emission (scoped ticket v0.3, kernel guard, review pass)
- Profile resolution by path or `rexecop.profiles` entry point (`tecrax`)
- Declarative profile validation rules (YAML, not hardcoded domain logic in core)
- Domain-neutral `first-run-demo` and `runtime-fixture` examples for onboarding,
  lifecycle, policy and connector regressions; Tecrax product semantics live only
  in the external `tecrax` package
- Operational controls: approve, pause, resume, cancel, retry, rollback, queue, target lock, maintenance windows
- Runtime readiness CLI: explicit `--root` / `REXECOP_ROOT`, named local
  `--instance` / `REXECOP_INSTANCE`, `init`, `doctor`, `env lint`, and
  `profile lint`
- Runtime worker: `rexecop worker run`, `rexecop queue --drain`, `rexecop trigger` (host-owned scheduling)
- Connectors: `mock`, config-driven `http_api` (retry, pagination, error mapping), `local_shell_readonly`, `ssh_readonly` (temporary; bounded output + digests)
- Execution contracts: digest-bound `ExecutionRequest` / `ExecutionReceipt` in workflow `shared_state` (schema `v0.2`)
- GovEngine `PolicyEngine` when `environment.policy_pack` is set: plan admission,
  supported neutral controls, pre-execution drift validation, and per-connector invoke gate
- Operator target catalog and profile-derived operation catalog with deterministic applicability
  and start-time drift rejection; catalog compatibility never replaces GovEngine admission
- Read-only action metadata UX: `action list`, `action show`, `action preview`,
  `action configure --dry-run`, `action validate`, and `secrets suggest-ref`
  expose profile/env/catalog action contracts, redacted effective-call previews,
  secret-ref name suggestions and bounded patch operations without backend IO,
  connector config values, GovEngine admission claims, or SCLite truth emission
- Storage: `FileStore` (default) or optional `SqliteStore` (`REXECOP_STORAGE` / `--storage`)
- Secrets port: `REXECOP_SECRET_*` and `REXECOP_SECRETS_FILE` (no plaintext secrets in git or `.rexecop/`)
- Operator CLI (`rexecop`); runtime data under an explicit root, named local
  instance, or default `.rexecop/` in the current working directory

## What RExecOp does not include

- A policy engine (GovEngine is the governance authority)
- SCLite schema authority or long-term truth storage
- Domain profiles in core (no Tecrax/Ravenclaw operational logic in `src/rexecop`)
- Production cron/recurrence scheduler (host-owned worker + systemd/cron pattern only)
- Web UI or multi-tenant RBAC
- Unattended apply on critical infrastructure without operator and governance gates

## Installation

Published alpha package:

```bash
python -m pip install "rexecop==0.2.11a0"
rexecop version
```

The published `0.2.11a0` wheel contains the full B2 enforcement path, R4c catalog, watchdog decision truth path, and manual recovery record path for the single supported alpha stack line.

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
pip install "rexecop[tecrax]==0.2.11a0"
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

## CLI commands

| Command | Purpose |
| --- | --- |
| `init` | Create the runtime root layout without secrets or backend IO |
| `doctor` | Check runtime root, storage, package compatibility, profile, env, catalog and secret refs |
| `env lint` | Validate environment YAML and inline secret hygiene |
| `secrets doctor` | Check secret refs, file permissions, duplicates and redaction (no values printed) |
| `secrets suggest-ref` | Suggest secret reference names from env connector shape without reading values |
| `profile lint` | Validate profile conformance for `readonly`, `mutation` or `all` tracks |
| `profile manifest` | Emit extension manifest `v0.1` for profiles, plugins and resolvers |
| `profile harness` | Run workflow test harness (dry-run fixture, evidence, bundle, policy-blocked path) |
| `profiles list` / `profiles show` | Discover registered profiles, intents, tracks and developer-check metadata |
| `connectors list` / `connectors show` | Discover connector backends, modes and certification tier |
| `capabilities list` | List neutral runtime capabilities and their source |
| `action list` / `action show` | Inspect profile/env/catalog action metadata, refs and backend constraints without backend IO |
| `action preview` | Show redacted HTTP/shell/SSH effective-call previews and bounded-output policy without backend IO |
| `action configure` | Generate bounded dry-run patch operations for action config; `--write-patch` writes the patch file only |
| `action validate` | Validate profile/env action bindings and catalog applicability without backend IO |
| `policy explain` | Show GovEngine policy reasoning for one operation-shaped request without execution |
| `operation explain` | Explain a stored operation plan, expected artifacts, bindings and safe next actions |
| `operation review` | Decision screen for a stored plan (`--format json\|table\|markdown`) before start |
| `operation diff` | Compare stored plan bindings vs current profile/env/catalog (`--format json\|table\|markdown`) |
| `runbook show` | Show profile-owned runbook ref and bounded content for one intent |
| `runtime status` | Runtime queue, active operations, locks and dead-letter summary (`--json`) |
| `ops` | Aggregate queue, blockers, dead letters, stale locks and action-required items |
| `dead-letter list` / `dead-letter show` | Inspect watchdog dead-letter items (redacted show) |
| `locks list` | List advisory target locks and stale holders |
| `explain-error` | Map operation/dead-letter/watchdog ref to failure class and safe next actions |
| `runtime recover` | Reconcile stale leases, interrupted operations and receipt gaps after restart (`--json`) |
| `backup create` / `backup restore` | Secret-scanned runtime store tarball + manifest restore |
| `watchdog manual-record` | Record a governed manual watchdog decision without executing recovery |
| `plan` | Create operation + plan; evaluate configured PolicyEngine and mutating admission gates |
| `approve` | Manual approval after `approval_required` |
| `start` | Execute workflow (queues when lock/capacity busy) |
| `pause` / `resume` | Pause only at `pause_safe` workflow steps |
| `cancel` | Abort before completion |
| `retry` | Operator retry when profile policy allows |
| `rollback` | Run explicit workflow rollback steps after failure |
| `validate` | Re-run declarative profile validation |
| `escalate` | Build operator escalation package |
| `queue` | Inspect FIFO run-now backlog; `queue --drain` processes pending starts |
| `worker run` | Poll queue and start approved operations (`--once`, `--poll-interval`, `--watch-inbox`, `--watchdog`, `--inbox-retry-budget`) |
| `trigger` | Create operation from JSON stdin or CLI flags (webhook-friendly) |
| `targets list` / `targets show` | Query bounded descriptors from a private target catalog |
| `operations list` / `operations explain` | Query profile-owned operations and target applicability |
| `operations unavailable` | List operations not technically applicable to one catalog target |
| `status` / `history` | Operation state and evidence history |
| `version` | Package version |

Global option: `--root` selects an explicit runtime root, `--instance` selects a named
local instance under `./.rexecop/instances/`, and `--storage file|sqlite` selects the
runtime storage backend.

## Development

```bash
pip install -e /path/to/tecrax -e ".[dev]"
python scripts/validate_public_truth.py
python scripts/validate_first_run_smoke.py
ruff check .
mypy src/rexecop
python -m build && python -m twine check dist/*
pytest
```

GitHub Actions runs on every push and pull request: install `tecrax`, public truth validation,
stack contract validation, profile conformance, first-run smoke, ruff, mypy, core boundary
grep, secret scan, pytest, and a `package-dry-run` job (`build` + `twine check`).

## Documentation

- [Deterministic reaction interpreter](docs/reaction-interpreter.md)

| Document | Topic |
| --- | --- |
| [docs/first-run.md](docs/first-run.md) | No-I/O onboarding: init, doctor, lint, plan |
| [docs/runtime-recovery-ops.md](docs/runtime-recovery-ops.md) | Triage, explain-error, recovery, backup and watchdog manual-record |
| [docs/architecture.md](docs/architecture.md) | Layer boundaries and execution path |
| [docs/stack-contract-compatibility.md](docs/stack-contract-compatibility.md) | Cross-repo contract matrix and readiness labels |
| [docs/operation-lifecycle.md](docs/operation-lifecycle.md) | States, CLI orchestration, queue/lock |
| [docs/operator-scheduler-pattern.md](docs/operator-scheduler-pattern.md) | Host-owned scheduling with worker/systemd |
| [docs/govengine-integration.md](docs/govengine-integration.md) | Governance port and apply gating |
| [docs/sclite-integration.md](docs/sclite-integration.md) | Artifact emission and authority model |
| [docs/evidence-model.md](docs/evidence-model.md) | Internal events vs SCLite truth |
| [docs/profile-contract.md](docs/profile-contract.md) | Profile layout and entry points |
| [docs/profile-developer-surface.md](docs/profile-developer-surface.md) | Profiles/connectors/capabilities discoverability and extension manifest |
| [docs/secrets-operator.md](docs/secrets-operator.md) | `secrets doctor`, ref resolution and file policy |
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
