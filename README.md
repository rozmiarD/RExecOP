# RExecOp

[![CI: pytest](https://github.com/rozmiarD/RExecOP/actions/workflows/ci.yml/badge.svg)](https://github.com/rozmiarD/RExecOP/actions/workflows/ci.yml)
[![Package: rexecop 0.1.2a0](https://img.shields.io/badge/package-rexecop%200.1.2a0-blueviolet.svg)](pyproject.toml)
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
| Version | `0.1.2a0` |
| Maturity | **alpha** — operator evaluation with documented limits |
| Roadmap | Phases 0–10 complete on `main` |
| Tests | 103 pytest tests (102 passed; CI: ruff, mypy, boundary grep, secret scan, pytest) |
| PyPI | not published — install from source |
| Default posture | `dry_run` / read-only first; `apply` requires GovEngine allow |

## Project sentence

> RExecOp runs profile-defined operations under GovEngine admission and records auditable outcomes through SCLite — profiles own meaning, GovEngine owns governance, SCLite owns proof, RExecOp owns execution mechanics.

## Stack position

```text
Profiles (tecrax, fixtures)
  -> RExecOp (plan, lifecycle, execution, validation)
  -> GovEngine (admission, governance meaning)
  -> RExecOp (maps lifecycle to artifacts)
  -> SCLite (contracts, tickets, receipts, review bundles)
```

| Layer | Responsibility |
| --- | --- |
| **Profiles** | Intents, workflows, connector contracts, declarative validation rules |
| **RExecOp** | Operation lifecycle, planning, step dispatch, pause/resume/retry, queue/lock, escalation |
| **GovEngine** | Policy interpretation, admission, runner request/receipt contracts — does not execute |
| **SCLite** | Auditable artifacts, scoped tickets, receipt-bounded evidence, review bundles |

Tecrax ships as the [`tecrax`](https://github.com/rozmiarD/tecrax) package (`rexecop.profiles:tecrax`).
Ravenclaw is legacy and out of scope for RExecOp.

## What RExecOp includes now

- Deterministic operation state machine and `OperationPlan` runtime artifact
- GovEngine port: real `GovEngineClient` + bootstrap-only `StaticGovEngineAdapter`
- SCLite port: full GovEngine-integration bundle emission (scoped ticket v0.3, kernel guard, review pass)
- Profile resolution by path or `rexecop.profiles` entry point (`tecrax`)
- Declarative profile validation rules (YAML, not hardcoded domain logic in core)
- Vertical slices: read-only `check_backup_status`, apply `restart_zabbix_agent` (mock + staging `http_api`)
- Operational controls: approve, pause, resume, cancel, retry, rollback, queue, target lock, maintenance windows
- Connectors: `mock`, config-driven `http_api`, `local_shell_readonly`
- Secrets port: `REXECOP_SECRET_*` and `REXECOP_SECRETS_FILE` (no plaintext secrets in git or `.rexecop/`)
- Operator CLI (`rexecop`) and file-based storage under `.rexecop/`

## What RExecOp does not include

- A policy engine (GovEngine is the governance authority)
- SCLite schema authority or long-term truth storage
- Domain profiles in core (no Tecrax/Ravenclaw operational logic in `src/rexecop`)
- Production scheduler daemon, web UI, multi-tenant RBAC, or LLM execution loops
- Unattended apply on critical infrastructure without operator and governance gates
- PyPI release (reserved for alpha gate / explicit operator approval)

## Installation

From source (recommended):

```bash
git clone https://github.com/rozmiarD/RExecOP.git
cd RExecOP
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

With the Tecrax profile package:

```bash
pip install -e ".[dev]" -e /path/to/tecrax
# or: pip install -e ".[dev,tecrax]"  # when tecrax is installable from index
```

CI also checks out [`tecrax`](https://github.com/rozmiarD/tecrax) for integration tests.

## Quick start

```bash
rexecop version

rexecop plan \
  --profile tecrax \
  --env examples/environments/small-public-unit-proxmox.example.yaml \
  --intent check_backup_status \
  --target all_critical_vms \
  --mode dry_run

rexecop start --operation <operation-id>
rexecop status --operation <operation-id>
rexecop validate --operation <operation-id>
```

- With `tecrax` installed, `--profile tecrax` resolves via entry point.
- For offline tests without the external package, use `examples/profiles/tecrax-fixture/profile.yaml`.
- Staging `http_api` template: `examples/environments/small-public-unit-proxmox.staging.example.yaml`

Runtime artifacts live under `.rexecop/` (gitignored): operations, evidence, SCLite bundles, receipt exports.

## CLI commands

| Command | Purpose |
| --- | --- |
| `plan` | Create operation + plan; GovEngine gate for mutating modes |
| `approve` | Manual approval after `approval_required` |
| `start` | Execute workflow (queues when lock/capacity busy) |
| `pause` / `resume` | Pause only at `pause_safe` workflow steps |
| `cancel` | Abort before completion |
| `retry` | Operator retry when profile policy allows |
| `rollback` | Run explicit workflow rollback steps after failure |
| `validate` | Re-run declarative profile validation |
| `escalate` | Build operator escalation package |
| `queue` | Inspect FIFO run-now backlog |
| `status` / `history` | Operation state and evidence history |
| `version` | Package version |

## Development

```bash
pip install -e /path/to/tecrax -e ".[dev]"
ruff check .
mypy src/rexecop
pytest
```

GitHub Actions runs on every push and pull request: install `tecrax`, ruff, mypy, core boundary grep (`tecrax_profile` / `import tecrax` forbidden in core), pytest.

## Documentation

| Document | Topic |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Layer boundaries and execution path |
| [docs/operation-lifecycle.md](docs/operation-lifecycle.md) | States, CLI orchestration, queue/lock |
| [docs/govengine-integration.md](docs/govengine-integration.md) | Governance port and apply gating |
| [docs/sclite-integration.md](docs/sclite-integration.md) | Artifact emission and authority model |
| [docs/evidence-model.md](docs/evidence-model.md) | Internal events vs SCLite truth |
| [docs/profile-contract.md](docs/profile-contract.md) | Profile layout and entry points |
| [docs/connector-contract.md](docs/connector-contract.md) | `http_api`, secrets, error taxonomy |
| [docs/safety-model.md](docs/safety-model.md) | Hard safety rules and operator posture |
| [docs/known-limitations.md](docs/known-limitations.md) | Alpha scope and explicit non-claims |
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
