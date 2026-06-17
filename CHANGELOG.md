# Changelog

All notable changes to RExecOp (`rexecop`) are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Versioning: `0.1.0a0` declares the **alpha gate** (roadmap Phase 10). Prior `0.x.0a0` lines
tracked incremental roadmap delivery.

## [0.1.4a2] - 2026-06-17

### Phase 13.3 — fixture path isolation

- `REXECOP_FIXTURE_GUARD_KEY` moved to `fixture_bundle.py` (tests/lab only)
- Production `emit_operation_bundle` skips kernel guard unless `REXECOP_KERNEL_GUARD_KEY` is set
- `emit_fixture_operation_bundle` for CI/lab bundles with fixture HMAC sidecar
- `export_placeholder_receipt` deprecated; implementation in `rexecop.examples.bootstrap_receipt`
- CI boundary grep: fixture key must not appear in `full_bundle.py`

## [0.1.4a1] - 2026-06-17

### Phase 13.2 — execution receipt honesty

- `executed_command_count` and `network_execution_performed` derived from connector
  `step_completed` evidence and `shared_state.connector_results`
- Ticket `max_runs` aligned with planned connector step count; relaxed strict profile for multi-connector plans
- Dry-run receipts keep `receipt_does_not_claim_live_target_execution` non-claim
- E2E assertions on staging `http_api` receipts

## [0.1.4a0] - 2026-06-17

### Phase 13.1 — SQLite storage backend

- `SqliteStore` implementing `OperationStoragePort` for operations, plans, and evidence
- Storage factory: `REXECOP_STORAGE=file|sqlite` or CLI `--storage`
- SCLite bundles, receipts, approvals, queue, and locks remain on disk under `.rexecop/`
- Parametrized tests: file vs sqlite backend parity

## [0.1.3a0] - 2026-06-17

### Phase 12 — runtime worker & triggery

- `rexecop worker run` with `--once`, `--poll-interval`, `--max-iterations`, `--watch-inbox`
- `rexecop queue --drain` one-shot queue processing
- `rexecop trigger` from JSON stdin or CLI flags; evidence `operation_triggered`
- `docs/operator-scheduler-pattern.md` (systemd/cron pattern — host-owned scheduling)

## [0.1.2a0] - 2026-06-17

### Phase 11 — neutral core

- Internal action plugin registry (`rexecop.internal_actions` entry points)
- Connector fixture loader (`rexecop.connector_backends` entry points)
- Generic `MockConnectorRuntime` in core; domain mock moved to `tecrax` (`tecrax_fixture`)
- `http-health-fixture` profile + `http_health_check` golden-path E2E (http_api-only)
- `InMemoryStore` for tests; storage boundary documented
- `OPERATOR_LAB_RUNBOOK.md` for lab validation
- Requires `tecrax>=0.3.1a0` for domain handlers and offline fixture mock

## [0.1.1a0] - 2026-06-17

### Profile consolidation

- Tecrax RExecOp profile now ships in [`tecrax`](https://github.com/rozmiarD/tecrax) (`tecrax:profile_root`)
- Optional dependency `tecrax>=0.3.0a0` replaces `tecrax-profile`
- CI checks out `rozmiarD/tecrax` instead of `tecrax-profile`
- Docs and runbook updated; `tecrax-profile` repo retired

## [0.1.0a0] - 2026-06-17

### Alpha gate (Phase 10)

- Declares RExecOp **alpha** for operator evaluation with documented limits
- Adds `OPERATOR_RUNBOOK.md`, `docs/known-limitations.md`, `CHANGELOG.md`
- CI: basic secret scan (`scripts/secret_scan.sh`), package install smoke
- Version reset to `0.1.0a0` as the alpha release line

### Included from Phases 0–9

- Operation core: state machine, `OperationPlan`, file storage, evidence with redaction
- GovEngine port: real `GovEngineClient` + bootstrap `StaticGovEngineAdapter`
- SCLite port: full GovEngine-integration bundle (scoped ticket v0.3, review pass)
- Vertical slices: `check_backup_status` (read-only), `restart_zabbix_agent` (apply)
- Orchestration: approve, pause, resume, cancel, retry, rollback, queue, target lock, maintenance
- External `tecrax-profile` package integration (`rexecop.profiles` entry point)
- Connectors: `mock`, `http_api`, `local_shell_readonly`; secrets port
- 97 pytest tests; document truth pass on README and `docs/`

### Alpha claims

Allowed: GovEngine-bound control-plane, profile-defined workflows, SCLite emission on
completion, mock and `http_api` read-only paths.

Not claimed: production governance authority, full Tecrax product, HA scheduler, UI,
unmanned apply on critical targets.

## [0.11.0a0] - 2026-06-17

### Phase 9 — Production connectors

- `http_api` config-driven REST connector
- `local_shell_readonly` allowlisted commands
- `CompositeConnectorRuntime` and secrets port (`REXECOP_SECRET_*`, `REXECOP_SECRETS_FILE`)
- Staging environment template and E2E tests

## [0.10.0a0] - 2026-06-17

### Phase 8 — Tecrax profile package

- External `tecrax-profile` repo with `rexecop.profiles` entry point
- Profile resolver and declarative validation rules in profile YAML
- CI boundary grep for domain imports in core

## [0.9.0a0] - 2026-06-16

### Phase 6 — Operational maturity

- Target lock, FIFO queue, maintenance windows, rollback executor
- `OperationStoragePort` protocol; CLI `retry`, `rollback`, `queue`

## [0.8.0a0] - 2026-06-16

### Phase 5 — Apply vertical slice

- `restart_zabbix_agent` apply workflow; approve, pause, resume, retry, cancel

## [0.7.0a0] - 2026-06-16

### Phase 3C — Full SCLite bundle

- GovEngine-integration parity bundle; `review_bundle` pass

## [0.6.0a0] - 2026-06-16

### Phase 4 — Read-only vertical slice

- Orchestrator, mock connectors, `check_backup_status` E2E

## [0.5.0a0] - 2026-06-16

### Phase 3B — SCLite emitter

- Real SCLite artifact emission on completion path

## [0.4.0a0] - 2026-06-16

### Phase 3A — SCLite placeholder port

- Placeholder emitter with schema refs (deprecated path)

## [0.3.0a0] - 2026-06-16

### Phase 2B — GovEngine client

- Real `GovEngineClient` adapter

## Earlier

- Phases 0–2A: repository bootstrap, operation core, static GovEngine gating

[0.1.0a0]: https://github.com/rozmiarD/RExecOP/compare/f483bed...75eb006
