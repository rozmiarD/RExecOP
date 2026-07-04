# Known limitations (alpha)

RExecOp `0.2.11a0` is the current source alpha line for operator evaluation.
This document states what the current published alpha line does **not** provide so expectations stay aligned with implementation.

## Governance and truth

| Limitation | Detail |
| --- | --- |
| GovEngine is authority | RExecOp does not interpret organizational policy; `StaticGovEngineAdapter` is test-only |
| SCLite is truth | Receipt exports under `<root>/receipts/` are summaries; bundles under `<root>/sclite/` are authoritative |
| No second policy engine | Configured policy packs and all mutating admission go through GovEngine — no bypass API |

## Operations and runtime

| Limitation | Detail |
| --- | --- |
| Host-owned worker only | `rexecop worker run` polls the file queue; no built-in cron/recurrence DSL |
| Runtime root is explicit but local | CLI supports global `--root`, `REXECOP_ROOT`, named `--instance` / `REXECOP_INSTANCE`, `init`, `doctor`, `env lint`, `profile lint`, and a fixture first-run path; this is runtime isolation, not multi-tenant RBAC |
| File storage default | `FileStore` is default; optional `SqliteStore` via `REXECOP_STORAGE=sqlite` |
| No web UI | CLI (`rexecop`) only |
| No multi-tenant RBAC | Single-operator storage model |
| Target lock is advisory | File-based lock per `(environment, target)` — not a distributed lock service |
| Catalog is operator-owned | Static local YAML projection only; no discovery, CMDB synchronization, UI or authorization cache |

## Connectors and infrastructure

| Limitation | Detail |
| --- | --- |
| `http_api` is generic REST | No built-in product SDKs — profiles declare actions and operators configure endpoints |
| Staging proven, production is operator-owned | CI uses HTTP stub; live infra requires operator runbook and secrets hygiene |
| `local_shell_readonly` only | No general shell apply backend in core |
| `ssh_readonly` is temporary | PolicyEngine gate when `policy_pack` set; allowlisted argv + read-only modes remain in connector |
| Static fixture is offline-only | `examples/first-run-demo/` and `examples/profiles/runtime-fixture/` use `static_fixture` for no-I/O onboarding and lifecycle regression |

## Profiles and domain

| Limitation | Detail |
| --- | --- |
| Tecrax via external package | Domain semantics in [`tecrax`](https://github.com/rozmiarD/tecrax), not in core |
| Ravenclaw out of scope | Legacy; no RExecOp profile path planned |
| Validation is declarative YAML | Complex domain logic beyond `require_*` steps belongs in profile tooling, not core |
| Operation catalog is opt-in | A profile intent must declare catalog metadata; RExecOp never invents missing domain applicability |

## Security and compliance

| Limitation | Detail |
| --- | --- |
| Secrets via operator config | `REXECOP_SECRETS_FILE` / env vars — no KMS/HSM integration |
| Redaction has finite detectors | Key names, resolved values and common provider/token patterns are covered; arbitrary unknown plaintext still requires bounded profile outputs |
| CI secret scan is heuristic | Full tracked tree/history scan covers common providers, private keys and credential assignments; it is not a KMS or external repository audit |
| Apply on critical targets | Requires explicit operator approval, GovEngine allow, and operational procedure — not unmanned |

## Distribution

| Limitation | Detail |
| --- | --- |
| Public PyPI | `rexecop==0.2.11a0` published for alpha evaluation — not a production-ready claim |
| Source alpha line | `0.2.11a0` is current on `main`; see [CHANGELOG.md](../CHANGELOG.md) for history |
| Coordinated dependencies | Source line requires `govengine==0.16.6` and `sclite-core==1.0.8`; the `tecrax` extra requires `tecrax==0.3.9a0` |

## Stack readiness labels

The current public stack baseline is recorded in
[stack-contract-compatibility.md](stack-contract-compatibility.md). Current active labels are:

- `alpha_readonly`
- `deterministic_plan_only`
- `deterministic_execute_readonly`

The labels `advisory_llm` and `mutation_ready` are not active. LLM output remains
an untrusted proposal shape only, and mutation/apply readiness is explicitly false.

## What alpha **does** provide (allowed claims)

- GovEngine-bound operations control-plane with default `GovEngineClient` adapter
- Profile-defined workflow execution and declarative validation
- SCLite artifact emission on the completion path with honest execution receipt metrics
- Connectors: `mock`, `http_api`, `local_shell_readonly`, temporary `ssh_readonly` (bounded output + digests)
- Workflow execution contracts: digest-bound `ExecutionRequest` / `ExecutionReceipt` in `shared_state` (schema `v0.2`)
- GovEngine `PolicyEngine` when `environment.policy_pack` is configured (operation admission/control projection + connector invoke)
- Host-owned worker, queue drain, and JSON `trigger` ingress
- Runtime readiness CLI: explicit `--root`, named `--instance`, `init`, `doctor`, `env lint`, `profile lint`
- Public-safe `examples/first-run-demo/` onboarding path with `scripts/validate_first_run_smoke.py`
- Optional SQLite storage backend for operations, plans, and evidence
- Wheel build + `twine check` validated in CI

## What alpha **does not** claim (forbidden marketing)

- Production-ready governance (GovEngine remains authority)
- Full Tecrax product or Ravenclaw merge
- Built-in cron/recurrence scheduler, HA multi-tenant control plane, or web UI
- Unmanned apply on critical targets
- Guarantee of production support or long-term PyPI semver stability

## Operator sign-off checklist

Before treating alpha as fit for your environment:

- [ ] Read [OPERATOR_RUNBOOK.md](../OPERATOR_RUNBOOK.md) and [safety-model.md](safety-model.md)
- [ ] Complete [OPERATOR_LAB_RUNBOOK.md](../OPERATOR_LAB_RUNBOOK.md) checklist
- [ ] Confirm GovEngine and SCLite versions match `pyproject.toml` pins
- [ ] Run a bounded read-only profile intent appropriate for the selected target
- [ ] Verify runtime root exports contain no plaintext secrets
- [ ] Accept alpha limits above for production-adjacent use
