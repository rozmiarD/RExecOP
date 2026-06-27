# Changelog

All notable changes to RExecOp (`rexecop`) are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

**Versioning:** pre-1.0 alpha tags use the `0.y.za0` form. Roadmap delivery before the
public alpha gate used **`0.3.0a0`–`0.11.0a0`** (Phases 2B–9; see [Pre-alpha gate history](#pre-alpha-gate-history)).
**`0.1.0a0`** (Phase 10) reset the public line and declared the alpha gate. The current
PyPI alpha line is **`0.2.6a0`**. Entries under [Releases](#releases) are newest first.

## Unreleased

- Added the PEP 561 `py.typed` marker so downstream stack profiles can type-check
  against RExecOp's exported modules instead of treating the package as untyped.
- Added `types-PyYAML` to the development extra so a clean `rexecop[dev]`
  environment can run `mypy src/rexecop` without manual stub installation.
- Added Tecrax Zabbix and Portainer HTTP action identity regression vectors that verify
  plan bindings, connector `action_contract_digest`, bounded receipt output digests and
  backend-not-called drift failures without adding domain semantics to RExecOp core.
- Updated Tecrax integration fixtures for the profile-owned bounded available-update
  summary action without adding host-update semantics to RExecOp core.

## [0.2.6a0] - 2026-06-24

- Advanced the source package beyond the published `0.2.5a0` wheel so B2,
  R4c, and their GovEngine `0.16.0` floor have an unambiguous release line.
- Published after GovEngine `0.16.0` passed its public-index install gate.
- Removed an import-order cycle between `rexecop.policy` and
  `rexecop.connectors` by lazily exposing the composite connector runtime.

### Full B2 policy binding and runtime enforcement

- `ExecutionRequest` / `ExecutionReceipt` schema `v0.2` binds GovEngine policy
  pack, verdict, enforcement-plan, and existing-admission digests; receipts also bind the
  request digest and carry a self-digest plus an enforcement summary.
- Operation-level `allow_with_obligations` supports GovEngine-projected
  `receipt_required`, `output_digest_required`, `output_limit`, `timeout`, and
  `max_steps`; unknown controls and binding drift fail closed before backend IO.
- Runtime output and internal-handler state deltas are redacted and bounded before
  entering `shared_state` or evidence. Oversized records roll back their state delta
  and retain only size, truncation status, and digest.
- Built-in shell, SSH, and HTTP connectors apply the tighter admitted timeout
  and output bound. Timeout controls reject unsupported plugin backends.
- SCLite execution contracts and receipts carry bounded GovEngine admission,
  pack, and verdict references; SCLite still owns artifact canonicalization and
  review truth.
- Added E2E, digest-drift, max-steps, backend-not-called, timeout, output-leak,
  and cross-repository Tecrax profile vectors.

### B2-lite enforcement hardening

- Structured argv restrictions block shell `-c`, `sudo`, service mutations, Docker
  mutations and Docker Compose lifecycle commands before subprocess execution
- Connector-level policy admission accepts only plain `allow`; unsupported
  connector controls fail closed. Supported operation-level controls are handled
  by the full B2 admission/enforcement path above.
- Negative matrices cover restricted argv, subprocess-not-called and PolicyEngine
  `allow_with_obligations` regression behavior
- Successful `http_api` responses are bounded by `max_response_bytes` before JSON parsing;
  oversized payloads fail without persistence
- Read-only connector steps may explicitly declare `metadata.continue_on_error`; failures
  remain in evidence and step receipts while diagnostic aggregation continues

### R0-lite and read-only profile enforcement

- Core boundary checks reject infrastructure-domain tokens under `src/rexecop`
- SCLite carrier references use the neutral `local_file_bundle` profile
- Profile connector contracts may bind actions to exact backend, command and argv shapes;
  environment allowlists that drift from those shapes fail at plan time
- Profiles may opt into strict intent-mode validation with `enforce_declared_modes: true`
- `known_hosts_policy: strict` maps to OpenSSH `StrictHostKeyChecking=yes`

### Secret and runtime artifact hardening

- Value-aware redaction for resolved secrets, provider tokens, connector output and errors
- Operator secrets files require current-user ownership, regular-file semantics and mode 0600
- Runtime directories/files and SQLite artifacts enforce modes 0700/0600
- Whole-environment inline-secret validation and history-aware CI secret scanning
- Source distributions exclude local editor workflow metadata

## Releases

### [0.2.5a0] - 2026-06-22

#### Deterministic reaction interpreter

- compiles a bounded, profile-owned reaction DSL with deterministic priority,
  exact operators, duplicate-condition rejection, and read-only intent resolution;
- enforces reaction depth, count, idempotency, and cycle boundaries fail-closed;
- requires plain GovEngine `allow` without obligations or constraints before a
  child operation can be planned;
- executes admitted reactions only through the normal operation lifecycle and
  binds the resulting receipt into a replayable SCLite reaction chain;
- validates LLM escalation proposals as untrusted, non-executable input only;
- adds `reaction-plan`, `reaction-start`, `reaction-replay`, and
  `reaction-proposal-validate` CLI commands.

### [0.2.4a0] - 2026-06-20

#### Execution request / receipt boundary

- `rexecop.execution.model`: `ExecutionRequest`, `ExecutionReceipt`, `ExecutionStepReceipt`, `ResourceLimits` (schema `v0.1`)
- `execution_request_from_workflow()` builds a domain-neutral request from planned workflow steps at run start
- `execution_receipt_from_results()` builds step receipts with `output_digest_refs` / `output_truncated` — no raw stdout/stderr in the receipt envelope
- `WorkflowRunner` stores `execution_request` and `execution_receipt` in `shared_state` on success and failure paths
- `rexecop.execution.output.bounded_text()`: UTF-8 byte cap (default 65536), full-output `sha256:` digest, truncation flags
- `local_shell_readonly` and `ssh_readonly`: bounded stdout/stderr plus `output_digests`, `output_truncated`, `output_sizes` (configurable `max_output_bytes`)
- Tests: `tests/test_execution_contracts.py`; connector output bounds in `test_http_api_connector.py`, `test_phase14_connectors.py`, `test_workflow_runner.py`
- Docs: [docs/execution-contract.md](docs/execution-contract.md)

#### GovEngine PolicyEngine end-to-end

- `environment.policy_pack` optional declarative pack; compiled at `plan`, stored on operation metadata
- Operation policy → `govengine_request_preview.policy_decision` for admission compose
- Connector policy gate in `CompositeConnectorRuntime.invoke()` before all backends
- `rexecop.policy` module; example [examples/policy/rexecop-connectors-default.yaml](examples/policy/rexecop-connectors-default.yaml)
- Tests: [tests/test_connector_policy_engine.py](tests/test_connector_policy_engine.py)
- Dependency pin: `govengine>=0.15.0,<0.16` (PolicyEngine MVP)
- Published to PyPI as `rexecop==0.2.4a0`

### [0.2.3a0] - 2026-06-20

#### Etap A — pre-policy contract hardening

- Environment target validation at `plan` (`environment.targets`, group members, `all_critical_vms` semantics)
- Workflow contract validation: missing/disabled connectors, unsupported step types
- `ssh_readonly`: configurable `known_hosts_policy`, `UserKnownHostsFile`, `shlex.quote` for remote commands
- `FileStore` atomic JSON writes via temp file + `os.replace`
- Docs: [environment-contract.md](docs/environment-contract.md), [storage-backends.md](docs/storage-backends.md)
- Tests: [tests/test_stage_a_contracts.py](tests/test_stage_a_contracts.py)
- Published to PyPI as `rexecop==0.2.3a0`

### [0.2.2a0] - 2026-06-20

#### Public PyPI (`15.1c`) and documentation clarity

- Clarify stack diagram: GovEngine gates mutating admission; RExecOp projects lifecycle into SCLite artifacts
- Publish `rexecop` to PyPI; update [docs/distribution.md](docs/distribution.md) and public-truth validators
- Canonical delivery test scope (`pytest -m delivery`) and composite runtime routing tests (from `0.2.1a0` batch)

### [0.2.1a0] - 2026-06-20

#### Domain connector backend plugin (`tecrax_proxmox`)

- `CompositeConnectorRuntime` routes `backend: <registered EP>` via `load_connector_backend_for_connector`
- Tecrax: `tecrax_proxmox` entry point builds Proxmox `http_api` config from templates
- Alpha sign-off: `docs/alpha-sign-off.md`, record template, `scripts/run_alpha_signoff_checks.sh`
- Delivery coverage: canonical scope in `tests/delivery_scope.py`, `pytest -m delivery`, `test_composite_runtime_routing.py`

### [0.2.0a0] - 2026-06-20

#### Phase 15 — distribution & E2E runbook

- CI `package-dry-run` job: `python -m build`, `twine check`, wheel install smoke
- [docs/distribution.md](docs/distribution.md): source, wheel, Git URL, private index guidance
- `OPERATOR_LAB_RUNBOOK.md`: full profile → GovEngine → SCLite E2E walkthrough
- Lab sections for GovEngine adapter posture and evidence vs SCLite authority
- Worker smoke checklist; package build smoke aligned with CI

### [0.1.5a0] - 2026-06-20

#### Phase 14 — connectors

- `http_api`: configurable retry backoff (`base_delay`, `max_delay`), action-level retry override
- `http_api`: optional pagination (`items_path`, `next_path`, `max_pages`)
- `http_api`: HTTP `error_class` mapping with redacted `body_snippet` on failures
- `local_shell_readonly`: allowlist validation via `govengine.execution.command_shape`
- `ssh_readonly` connector (temporary read-only allowlist; documented non-production policy path)
- Staging HTTP stub: paginated and transient/auth-error endpoints for lab tests
- Tecrax: `tecrax.connectors.proxmox.build_http_api_connector_config()` templates

### [0.1.4a2] - 2026-06-17

#### Phase 13.3 — fixture path isolation

- `REXECOP_FIXTURE_GUARD_KEY` moved to `fixture_bundle.py` (tests/lab only)
- Production `emit_operation_bundle` skips kernel guard unless `REXECOP_KERNEL_GUARD_KEY` is set
- `emit_fixture_operation_bundle` for CI/lab bundles with fixture HMAC sidecar
- `export_placeholder_receipt` deprecated; implementation in `rexecop.examples.bootstrap_receipt`
- CI boundary grep: fixture key must not appear in `full_bundle.py`

### [0.1.4a1] - 2026-06-17

#### Phase 13.2 — execution receipt honesty

- `executed_command_count` and `network_execution_performed` derived from connector
  `step_completed` evidence and `shared_state.connector_results`
- Ticket `max_runs` aligned with planned connector step count; relaxed strict profile for multi-connector plans
- Dry-run receipts keep `receipt_does_not_claim_live_target_execution` non-claim
- E2E assertions on staging `http_api` receipts

### [0.1.4a0] - 2026-06-17

#### Phase 13.1 — SQLite storage backend

- `SqliteStore` implementing `OperationStoragePort` for operations, plans, and evidence
- Storage factory: `REXECOP_STORAGE=file|sqlite` or CLI `--storage`
- SCLite bundles, receipts, approvals, queue, and locks remain on disk under `.rexecop/`
- Parametrized tests: file vs sqlite backend parity

### [0.1.3a0] - 2026-06-17

#### Phase 12 — runtime worker & triggery

- `rexecop worker run` with `--once`, `--poll-interval`, `--max-iterations`, `--watch-inbox`
- `rexecop queue --drain` one-shot queue processing
- `rexecop trigger` from JSON stdin or CLI flags; evidence `operation_triggered`
- `docs/operator-scheduler-pattern.md` (systemd/cron pattern — host-owned scheduling)

### [0.1.2a0] - 2026-06-17

#### Phase 11 — neutral core

- Internal action plugin registry (`rexecop.internal_actions` entry points)
- Connector fixture loader (`rexecop.connector_backends` entry points)
- Generic `MockConnectorRuntime` in core; domain mock moved to `tecrax` (`tecrax_fixture`)
- `http-health-fixture` profile + `http_health_check` golden-path E2E (http_api-only)
- `InMemoryStore` for tests; storage boundary documented
- `OPERATOR_LAB_RUNBOOK.md` for lab validation
- Requires `tecrax>=0.3.1a0` for domain handlers and offline fixture mock

### [0.1.1a0] - 2026-06-17

#### Profile consolidation

- Tecrax RExecOp profile now ships in [`tecrax`](https://github.com/rozmiarD/tecrax) (`tecrax:profile_root`)
- Optional dependency `tecrax>=0.3.0a0` replaces `tecrax-profile`
- CI checks out `rozmiarD/tecrax` instead of `tecrax-profile`
- Docs and runbook updated; `tecrax-profile` repo retired

### [0.1.0a0] - 2026-06-17

#### Alpha gate (Phase 10)

- Declares RExecOp **alpha** for operator evaluation with documented limits
- Adds `OPERATOR_RUNBOOK.md`, `docs/known-limitations.md`, `CHANGELOG.md`
- CI: basic secret scan (`scripts/secret_scan.sh`), package install smoke
- Version reset to `0.1.0a0` as the alpha release line

#### Included from Phases 0–9

- Operation core: state machine, `OperationPlan`, file storage, evidence with redaction
- GovEngine port: real `GovEngineClient` + bootstrap `StaticGovEngineAdapter`
- SCLite port: full GovEngine-integration bundle (scoped ticket v0.3, review pass)
- Vertical slices: `check_backup_status` (read-only), `restart_zabbix_agent` (apply)
- Orchestration: approve, pause, resume, cancel, retry, rollback, queue, target lock, maintenance
- External `tecrax-profile` package integration (`rexecop.profiles` entry point)
- Connectors: `mock`, `http_api`, `local_shell_readonly`; secrets port
- 97 pytest tests; document truth pass on README and `docs/`

#### Alpha claims

Allowed: GovEngine-bound control-plane, profile-defined workflows, SCLite emission on
completion, mock and `http_api` read-only paths.

Not claimed: production governance authority, full Tecrax product, HA scheduler, UI,
unmanned apply on critical targets.

## Pre-alpha gate history

Roadmap versions before the Phase 10 reset (`0.1.0a0`). Listed oldest → newest.

### [0.3.0a0] - 2026-06-16 — Phase 2B

- Real `GovEngineClient` adapter

### [0.4.0a0] - 2026-06-16 — Phase 3A

- Placeholder SCLite emitter with schema refs (deprecated path)

### [0.5.0a0] - 2026-06-16 — Phase 3B

- Real SCLite artifact emission on completion path

### [0.6.0a0] - 2026-06-16 — Phase 4

- Orchestrator, mock connectors, `check_backup_status` E2E

### [0.7.0a0] - 2026-06-16 — Phase 3C

- GovEngine-integration parity bundle; `review_bundle` pass

### [0.8.0a0] - 2026-06-16 — Phase 5

- `restart_zabbix_agent` apply workflow; approve, pause, resume, retry, cancel

### [0.9.0a0] - 2026-06-16 — Phase 6

- Target lock, FIFO queue, maintenance windows, rollback executor
- `OperationStoragePort` protocol; CLI `retry`, `rollback`, `queue`

### [0.10.0a0] - 2026-06-17 — Phase 8

- External `tecrax-profile` repo with `rexecop.profiles` entry point
- Profile resolver and declarative validation rules in profile YAML
- CI boundary grep for domain imports in core

### [0.11.0a0] - 2026-06-17 — Phase 9

- `http_api` config-driven REST connector
- `local_shell_readonly` allowlisted commands
- `CompositeConnectorRuntime` and secrets port (`REXECOP_SECRET_*`, `REXECOP_SECRETS_FILE`)
- Staging environment template and E2E tests

### Earlier

- Phases 0–2A: repository bootstrap, operation core, static GovEngine gating

[0.2.6a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.5a0...v0.2.6a0
[0.2.5a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.4a0...v0.2.5a0
[0.2.4a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.3a0...v0.2.4a0
[0.2.3a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.2a0...v0.2.3a0
[0.2.2a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.1a0...v0.2.2a0
[0.2.1a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.0a0...v0.2.1a0
[0.2.0a0]: https://github.com/rozmiarD/RExecOP/compare/v0.1.5a0...v0.2.0a0
[0.1.0a0]: https://github.com/rozmiarD/RExecOP/compare/f483bed...75eb006
