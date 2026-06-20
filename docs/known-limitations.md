# Known limitations (alpha)

RExecOp `0.2.1a0` is an **alpha** release for operator evaluation. This document states
what the software does **not** provide so expectations stay aligned with implementation.

## Governance and truth

| Limitation | Detail |
| --- | --- |
| GovEngine is authority | RExecOp does not interpret organizational policy; `StaticGovEngineAdapter` is test-only |
| SCLite is truth | Receipt exports under `.rexecop/receipts/` are summaries; bundles under `.rexecop/sclite/` are authoritative |
| No second policy engine | All mutating gates go through `govengine_port` — no bypass API |

## Operations and runtime

| Limitation | Detail |
| --- | --- |
| Host-owned worker only | `rexecop worker run` polls the file queue; no built-in cron/recurrence DSL |
| Runtime root is cwd | `.rexecop/` is created in the current working directory — no global `--root` flag |
| File storage default | `FileStore` is default; optional `SqliteStore` via `REXECOP_STORAGE=sqlite` |
| No web UI | CLI (`rexecop`) only |
| No multi-tenant RBAC | Single-operator storage model |
| Target lock is advisory | File-based lock per `(environment, target)` — not a distributed lock service |

## Connectors and infrastructure

| Limitation | Detail |
| --- | --- |
| `http_api` is generic REST | No built-in Proxmox/PBS/Zabbix SDKs — operators configure actions in environment YAML |
| Staging proven, production is operator-owned | CI uses HTTP stub; live infra requires operator runbook and secrets hygiene |
| `local_shell_readonly` only | No general shell apply backend in core |
| `ssh_readonly` is temporary | Read-only SSH allowlist exists; full remote-command policy belongs in GovEngine |
| Mock remains default offline | `examples/...proxmox.example.yaml` uses `mock` backend for offline use |

## Profiles and domain

| Limitation | Detail |
| --- | --- |
| Tecrax via external package | Domain semantics in [`tecrax`](https://github.com/rozmiarD/tecrax), not in core |
| Ravenclaw out of scope | Legacy; no RExecOp profile path planned |
| Validation is declarative YAML | Complex domain logic beyond `require_*` steps belongs in profile tooling, not core |

## Security and compliance

| Limitation | Detail |
| --- | --- |
| Secrets via operator config | `REXECOP_SECRETS_FILE` / env vars — no KMS/HSM integration |
| Redaction is pattern-based | `redact_payload()` covers common key names; novel secret shapes may need profile discipline |
| Alpha CI secret scan is basic | `scripts/secret_scan.sh` — not a substitute for dedicated secret management review |
| Apply on critical targets | Requires explicit operator approval, GovEngine allow, and operational procedure — not unmanned |

## Distribution

| Limitation | Detail |
| --- | --- |
| Public PyPI not published | Wheels are built and checked in CI; install from source, Git URL, or operator mirror |
| Alpha semver line | `0.2.1a0` is the current alpha line; see [CHANGELOG.md](../CHANGELOG.md) for history |

## What alpha **does** provide (allowed claims)

- GovEngine-bound operations control-plane with default `GovEngineClient` adapter
- Profile-defined workflow execution and declarative validation
- SCLite artifact emission on the completion path with honest execution receipt metrics
- Connectors: `mock`, `http_api`, `local_shell_readonly`, temporary `ssh_readonly`
- Host-owned worker, queue drain, and JSON `trigger` ingress
- Optional SQLite storage backend for operations, plans, and evidence
- Wheel build + `twine check` validated in CI

## What alpha **does not** claim (forbidden marketing)

- Production-ready governance (GovEngine remains authority)
- Full Tecrax product or Ravenclaw merge
- Built-in cron/recurrence scheduler, HA multi-tenant control plane, or web UI
- Unmanned apply on critical targets
- Public PyPI availability

## Operator sign-off checklist

Before treating alpha as fit for your environment:

- [ ] Read [OPERATOR_RUNBOOK.md](../OPERATOR_RUNBOOK.md) and [safety-model.md](safety-model.md)
- [ ] Complete [OPERATOR_LAB_RUNBOOK.md](../OPERATOR_LAB_RUNBOOK.md) checklist
- [ ] Confirm GovEngine and SCLite versions match `pyproject.toml` pins
- [ ] Run read-only `check_backup_status` on fixture or staging `http_api`
- [ ] Verify `.rexecop/` exports contain no plaintext secrets
- [ ] Accept alpha limits above for production-adjacent use
