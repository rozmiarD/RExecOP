# Known limitations (alpha)

RExecOp `0.1.1a0` is an **alpha** release for operator evaluation. This document states
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
| No scheduler daemon | Queue is FIFO file-based; no cron, no recurring jobs, no background worker |
| File storage default | `FileStore` only; `OperationStoragePort` exists but SQLite/remote backends are not shipped |
| No web UI | CLI (`rexecop`) only |
| No multi-tenant RBAC | Single-operator file store model |
| Target lock is advisory | File-based lock per `(environment, target)` — not a distributed lock service |

## Connectors and infrastructure

| Limitation | Detail |
| --- | --- |
| `http_api` is generic REST | No built-in Proxmox/PBS/Zabbix SDKs — operators configure actions in environment YAML |
| Staging proven, production is operator-owned | CI uses HTTP stub; live infra requires operator runbook and secrets hygiene |
| `local_shell_readonly` only | No general shell apply backend in core |
| No SSH apply connector | Read-only SSH not implemented in alpha |
| Mock remains default | `examples/...proxmox.example.yaml` uses `mock` backend for offline use |

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
| PyPI not published | Install from source; `pip install rexecop` metadata not validated on public index |
| Alpha semver line | `0.1.0a0` resets marketing version; prior `0.11.0a0` was roadmap delivery numbering |

## Alpha claims (allowed)

- GovEngine-bound operations control-plane
- Profile-defined workflow execution
- SCLite artifact emission on completion path
- Mock connectors and `http_api` read-only paths (staging-tested)

## Alpha claims (forbidden — do not document or market)

- Production-ready governance (GovEngine remains authority)
- Full Tecrax product
- HA scheduler / multi-tenant / UI
- Unmanned apply on critical targets

## Operator sign-off checklist

Before treating alpha as fit for your environment:

- [ ] Read [OPERATOR_RUNBOOK.md](../OPERATOR_RUNBOOK.md) and [safety-model.md](safety-model.md)
- [ ] Confirm GovEngine and SCLite versions match `pyproject.toml` pins
- [ ] Run read-only `check_backup_status` on fixture or staging `http_api`
- [ ] Verify `.rexecop/` exports contain no plaintext secrets
- [ ] Accept alpha limits above for production-adjacent use
