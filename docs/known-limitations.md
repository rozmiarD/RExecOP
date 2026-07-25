# Known limitations (alpha)

RExecOp `0.3.0rc3` is the current source alpha candidate for operator evaluation.
This document states what the current published alpha line does **not** provide so expectations stay aligned with implementation.

## Governance and truth

| Limitation | Detail |
| --- | --- |
| GovEngine is authority | RExecOp does not interpret organizational policy; `StaticGovEngineAdapter` is test-only |
| Stable runtime is read-only | `REXECOP_MUTATION_POSTURE` defaults to `stable_read_only`; `apply` and `recovery` fail with `mutation_not_certified` before execution and are rechecked before connector IO, regardless of a positive GovEngine decision |
| Signed-decision host adapter required for mutation | Source supports the canonical verify/bind/claim path programmatically; the CLI does not yet configure production signer/verifier/trust adapters, so mutating connector IO fails closed |
| SCLite is truth | Receipt exports under `<root>/receipts/` are summaries; bundles under `<root>/sclite/` are authoritative |
| No second policy engine | Configured policy packs and all mutating admission go through GovEngine — no bypass API |

## Operations and runtime

| Limitation | Detail |
| --- | --- |
| Host-owned worker only | `rexecop worker run` polls the file queue; no built-in cron/recurrence DSL |
| Runtime root is explicit but local | CLI supports global `--root`, `REXECOP_ROOT`, named `--instance` / `REXECOP_INSTANCE`, `init`, `doctor`, `env lint`, `profile lint`, and a fixture first-run path; this is runtime isolation, not multi-tenant RBAC |
| Alpha roots do not migrate in place to 1.0 | The v1 compatibility policy requires a new runtime root; queue, lease, attempt and lifecycle state are not copied across major lines |
| One executor per root | Stable runtime certification covers `FileStore` with one active executor; multi-worker/distributed execution is blocked by `doctor` |
| SQLite alpha-only | `SqliteStore` is selectable via `REXECOP_STORAGE=sqlite`, but is not stable-runtime certified |
| Doctor security classification is local | `security_blockers` classifies fail-closed runtime configuration checks (mutation, plugins, network, inputs and stack compatibility); it is not a vulnerability scan or substitute for independent release review |
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
| Operator journey smoke is fixture-bound | `validate_operator_journeys.py` proves CLI chains on public fixtures; staging/Tecrax endpoints require separate lab runs ([OPERATOR_LAB_RUNBOOK.md](../OPERATOR_LAB_RUNBOOK.md)) |
| Fixture failure env is lab-only | `REXECOP_STATIC_FIXTURE_FAILURES` injects transient `static_fixture` errors for retry drills in tests/smoke — not for production connectors |

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
| Redaction has finite detectors | Exact-path `public_projection.safe_fields` is the disclosure boundary and undeclared values become digest-only; any deliberately allowlisted plaintext still relies on finite key/value detectors and operator review |
| DNS rebinding | Stable-live HTTP depends on operator-enforced DNS/egress controls; runtime and `doctor` fail closed when the dependency is undeclared, but transport-level DNS pinning is not claimed |
| CI secret scan is heuristic | Full tracked tree/history scan covers common providers, private keys and credential assignments; it is not a KMS or external repository audit |
| Apply on critical targets | Not stable-certified. The `lab_only` mechanics posture still requires explicit operator approval, GovEngine allow, trusted signed decision, atomic attempt claim, and operational procedure |

## Distribution

| Limitation | Detail |
| --- | --- |
| Public PyPI | `rexecop==0.2.24a0` published for alpha evaluation — not a production-ready claim |
| Source alpha line | `0.3.0rc3` is the local candidate on `main`; see [CHANGELOG.md](../CHANGELOG.md) for history |
| Public API is a candidate freeze | `rexecop.public_api.v1` identifies the intended 1.x import/CLI surface, but does not override the unreleased candidate status or other M10 blockers |
| Coordinated dependencies | Source line requires public `govengine==1.0.0rc1` and public `sclite-core==2.0.0`. RExecOp 1.x has no `tecrax` extra; Tecrax is a separately released external consumer/plugin. |
| Operational qualification | The current source candidate passed the M10 isolated clean-install, live bounded read-only, restart/recovery and public-projection disclosure journey recorded in [`release-qualification/m10-operational.json`](release-qualification/m10-operational.json). This is not the independent security review, does not certify mutation, and does not make the private runtime root publishable. |

## Stack readiness labels

The current public stack baseline is recorded in
[stack-contract-compatibility.md](stack-contract-compatibility.md). Current active labels are:

- `alpha_readonly`
- `deterministic_plan_only`
- `deterministic_execute_readonly`

The labels `advisory_llm` and `mutation_ready` are not active. LLM output remains
an untrusted proposal shape only. Mutation readiness is explicitly false and enforced
by the default runtime posture, not merely documented as an operator expectation.

## What alpha **does** provide (allowed claims)

- GovEngine-bound operations control-plane with default `GovEngineClient` adapter
- Profile-defined workflow execution and declarative validation
- SCLite artifact emission on the completion path with honest execution receipt metrics
- Signed-decision receipt conformance is active only when the host configures
  the authority, verifier and trust policy. It validates deterministic bindings
  and postconditions but cannot prove an already compromised runtime reported
  honest output metrics.
- Connectors: `mock`, `http_api`, `local_shell_readonly`, temporary `ssh_readonly` (bounded output + digests)
- Workflow execution contracts: digest-bound `ExecutionRequest` / `ExecutionReceipt` in `shared_state` (schema `v0.2`)
- GovEngine `PolicyEngine` when `environment.policy_pack` is configured (operation admission/control projection + connector invoke)
- Host-owned worker, queue drain, and JSON `trigger` ingress
- Runtime readiness CLI: explicit `--root`, named `--instance`, `init`, `doctor`, `env lint`, `profile lint`
- Public-safe `examples/first-run-demo/` onboarding path with `scripts/validate_first_run_smoke.py`
- §6 operator journey smoke with `scripts/validate_operator_journeys.py` (read-only execute, failure/triage, governance controls projection, audit CLI on fixtures)
- `rexecop governance controls` — operator-facing GovEngine typed-execution control catalog projection (non-authoritative)
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
