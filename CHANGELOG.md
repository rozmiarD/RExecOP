# Changelog

All notable changes to RExecOp (`rexecop`) are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

**Versioning:** pre-1.0 alpha tags use the `0.y.za0` form. Roadmap delivery before the
public alpha gate used **`0.3.0a0`–`0.11.0a0`** (Phases 2B–9; see [Pre-alpha gate history](#pre-alpha-gate-history)).
**`0.1.0a0`** (Phase 10) reset the public line and declared the alpha gate. The current
PyPI alpha line is **`0.2.14a0`**. Entries under [Releases](#releases) are newest first.

## Unreleased

- Added M7 audit CLI projections: `receipt show`, `evidence show`,
  `chain summary`, and required-redaction `support bundle --redacted` with
  stable JSON schemas, digest checks, bounded evidence previews, SCLite-ref
  integrity status, and no new truth-store ownership.
- Started M8 CLI contract closure with `rexecop.cli_contract_registry.v0.1`
  and `rexecop contracts cli`, a read-only machine-readable registry of
  operator-facing command schemas, formats, exit-code policy, redaction claims
  and authority boundaries.
- Added the initial `rexecop.cli_error.v0.1` envelope for selected
  operator-facing failure paths: missing operation explain, failed profile
  conformance, runtime blockers in `ops`, broken receipt digest, and
  unredacted support-bundle requests.

## [0.2.14a0] - 2026-07-04

- Published `rexecop==0.2.14a0` on PyPI with `govengine==0.16.8`, `sclite-core==1.0.8`
  and `tecrax==0.3.9a0` extra pin.
- Added `rexecop.profile_connector_execution_spec.v0.1` typed execution compilation
  for registered plugin connector backends.
- Allow registered plugin backends under `fixture_only` connector posture.
- Pass `registered_plugin_backend` metadata into GovEngine typed execution admission.

## [0.2.13a0] - 2026-07-04

- Published `rexecop==0.2.13a0` on PyPI with `govengine==0.16.7`, `sclite-core==1.0.8`
  and `tecrax==0.3.9a0` extra pin.
- Added M7 truth-path projection: `TruthPathProjection`, `project_truth_path()`,
  `rexecop operation truth-path --operation <id>` and golden fixture
  `tests/fixtures/truth_path_golden.json` for Tecrax `diagnose_monitoring_host`.
- Fixed truth-path digest normalization (`sha256:` prefix) and golden test reload
  after `export_receipt`.
- Completed M6.5 stack contract compatibility gates: expanded runtime projection
  matrix (typed execution, action configure CLI, doctor/explain outputs),
  SCLite artifact version pins, `evaluate_stack_contract_compatibility()`,
  golden fixture `tests/fixtures/stack_contract_compatibility_golden.json`,
  `contract_versions` in doctor/explain and CI validation in
  `scripts/validate_stack_contracts.py` with `unknown_major_fail_closed` policy.
- Added M6.5 stack contract compatibility: `rexecop_runtime_projection_matrix()`,
  `evaluate_govengine_contract_compatibility()` and `rexecop doctor` blocker
  `stack_contract_compatibility` consume GovEngine `supported_contract_report`.
- Policy-pack `output_digest_required` is carried in the typed execution overlay
  for receipt enforcement and does not block pre-IO governance admission.
- Typed execution governance overlay now consumes `policy_enforcement.plan.controls`
  via GovEngine `project_typed_execution_policy_overlay()` so policy-pack
  `output_digest_required`, `allowed_network_egress`, `no_raw_shell` and related
  controls flow into `admit_typed_execution()` before connector backend IO.
- Added typed execution stack compatibility check: RExecOp backend descriptors are
  evaluated against GovEngine typed execution control catalog via
  `evaluate_typed_execution_stack_compatibility()` and `rexecop doctor` blocker
  `typed_execution_stack_compatibility`.
- Added M6 typed execution governance bridge to GovEngine G5:
  `build_typed_execution_governance_request()`, `evaluate_typed_execution_governance()`
  and `enforce_typed_execution_governance()` project digest-bound typed specs into
  `admit_typed_execution()` before connector backend IO. Connector steps store
  `typed_execution_admissions` and fail closed with `policy_denied` when
  governance blocks raw shell, unsupported backends, missing output digest refs,
  network boundary mismatch or mutation without approval evidence.
- Bound typed execution digests into runtime `ExecutionReceipt`: per-step
  `execution_spec_digest` / `capability_descriptor_digest`, aggregate
  `typed_execution_binding` (`rexecop.typed_execution_binding.v0.1`) and
  SCLite export bridge `rexecop_runtime_binding` with policy/admission/output
  digest refs only (no typed payload ownership in RExecOp).
- Added M6 backend capability descriptors with identity class, egress/network
  boundary, secret-ref requirements and live-backend posture projections
  (`rexecop.backend_capability_descriptor.v0.1`). Typed execution compile
  binds capability digests before IO; raw shell and undeclared backend classes
  fail closed.
- Started M6 typed execution contracts in RExecOp: digest-bound
  `StepExecutionSpec` with `CommandExecutionSpec`, `HttpActionExecutionSpec`
  and `static_fixture` projections (`rexecop.execution.typed_spec`). Connector
  steps compile and bind per-step digests in `shared_state` before backend IO
  when `execution_context` is present; unknown major schema versions fail closed.
- Added `rexecop action policy-preview <intent> --target ID` to simulate
  GovEngine policy impact for one profile-owned action using digest-bound
  source contracts and redacted `PolicyEvaluationExplanation` output
  (`rexecop.action_policy_impact.v0.1`). Skips when `policy_pack` is absent;
  does not create execution requests, runtime admission or SCLite truth claims.
- Added M5 action template scope 1.0: built-in `http.simple-get`,
  `shell.readonly-allowlist` and `ssh.readonly-allowlist` skeletons exposed via
  `rexecop action templates list`, `action show` template provenance and
  `action configure --template` fallback when profile shapes are missing.
- Added `rexecop action diff <intent> --env <path>` to compare profile connector
  contracts against operator environment bindings for one intent. Output uses
  `rexecop.action_diff.v0.1`, reports per-step drift/incomplete checks, HTTP
  shape digests and an advisory `configure_hint` without backend IO or env mutation.

## [0.2.12a0] - 2026-07-04

- Refreshed README for current M1–M5 CLI surface: grouped overview, accurate capability
  list, and link to new [docs/cli-reference.md](docs/cli-reference.md); moved the full
  command table out of README and [docs/operation-lifecycle.md](docs/operation-lifecycle.md).
- Hardened M5 action surface validation and delivery coverage: `action validate`
  now fails on duplicate `secret_ref` reuse across connector bindings;
  `tests/test_action_surface.py` covers secret redaction, no backend IO, malformed
  env/action input, unknown configure templates, shape-digest drift and canonical
  `patch_digest` output; the module is registered in `tests/delivery_scope.py`
  (`action_surface` theme).
- Added M5 read-only action metadata UX: `rexecop action list`,
  `rexecop action show <intent>`, `rexecop action preview <intent>`, and
  `rexecop action validate --all|--intent`, plus `rexecop secrets suggest-ref`
  and dry-run `rexecop action configure <intent>`. The commands expose
  profile/env/catalog action descriptors, source contract digests, required
  secret refs, backend constraints, redacted effective-call previews and bounded
  patch operations without backend IO, GovEngine admission claims, SCLite truth
  emission or connector config values.
- Added M4 profile workflow test harness: `rexecop profile harness`, `run_profile_workflow_harness()`
  and `workflow_harness` output in `profiles show` / `developer_check`
  (`rexecop.profile_workflow_harness.v0.1`). Checks cover dry-run fixture execution,
  no-secret evidence, SCLite bundle shape and policy-blocked mutation paths.
- Added profile-owned `operator_metadata.yaml` projection with user-facing labels,
  runbook hints, safe next options and failure mapping. RExecOp loads and validates
  the document, surfaces it in `operations explain` (`rexecop.operation_profile_explain.v0.1`),
  `profiles show`, `operations unavailable`, `operation review` and `explain-error`.
- Wired GovEngine G3 `explain_profile_governance()` into `profiles show` and
  `run_profile_developer_check()` as `govengine_governance` compatibility output.
- Added M4 profile developer surface: categorized `profile lint` conformance,
  `rexecop profile manifest`, `profiles list/show`, `connectors list/show`,
  `capabilities list`, extension manifest `v0.1`, plugin compatibility report,
  and `run_profile_developer_check()` without a runtime store. Documented in
  [docs/profile-developer-surface.md](docs/profile-developer-surface.md).
- Added `rexecop secrets doctor` for missing refs, duplicate ref reuse, secrets
  file permissions, orphan file keys and redaction self-test without printing
  secret values. Documented in [docs/secrets-operator.md](docs/secrets-operator.md).
- Added `rexecop operations unavailable` for catalog target technical
  applicability reasoning with `why_unavailable` and `safe_next_options`.
  Documented in [docs/operator-catalog.md](docs/operator-catalog.md).
- Added M3 runtime triage commands: `rexecop runtime status --json`, `rexecop ops`,
  `rexecop dead-letter list/show`, `rexecop locks list`, and
  `rexecop explain-error <ref>` with bounded failure classes and safe next actions.
  Documented in [docs/runtime-recovery-ops.md](docs/runtime-recovery-ops.md).
- Added M3 recovery: `rexecop runtime recover --json`, idempotency keys, crash-safe
  receipt repair, `backup create/restore`, and worker startup hook. Wired GovEngine
  G2 `explain_supervisor_action()` into watchdog `explain-error` paths. Documented in
  [docs/runtime-recovery-ops.md](docs/runtime-recovery-ops.md) and
  [docs/govengine-integration.md](docs/govengine-integration.md).
- Added `rexecop operation diff --operation <id>` (M2) with stable JSON plus
  `--format table|markdown` to compare stored catalog/profile/environment
  bindings against the current operator state before start.
- Added `rexecop operation review --operation <id>` with stable JSON plus
  `--format table|markdown` decision screens for stored plans: digests,
  backends, runbook refs, stop conditions, expected evidence, governance
  blockers and safe next actions.
- Added `rexecop runbook show <intent> --profile <profile>` for profile-owned
  runbook metadata and bounded content bound to the profile digest.
- Extended `scripts/validate_first_run_smoke.py` to gate
  `operation explain`, `operation review` and `runbook show` after `plan`.
- Added `rexecop policy explain`, which consumes GovEngine
  `PolicyEvaluationExplanation` for one operation-shaped request and returns
  redacted JSON without reimplementing policy reasoning in RExecOp.
- Added `rexecop operation explain --operation <id>` with stable redacted JSON
  for stored plans: bindings/digests, GovEngine status, expected SCLite
  artifacts, safe next actions, and mutating contract readiness.
- Aligned documentation truth surface: runtime-root path convention (`<root>/`),
  `docs/first-run.md` in README index, expanded alpha sign-off gate list, archived
  the `0.2.9a0` sign-off record, and added `validate_first_run_smoke` to CI.
- Added first-run runtime readiness commands for source-line evaluation:
  global `--root`, `REXECOP_ROOT`, named `--instance` / `REXECOP_INSTANCE`,
  `rexecop init`, `rexecop init --guided`, and `rexecop doctor`.
- Added operator-input validation commands:
  `rexecop env lint` and `rexecop profile lint --track readonly|mutation|all`.
- Added the public-safe `examples/first-run-demo/` fixture path plus
  `scripts/validate_first_run_smoke.py`, now included in alpha sign-off, to
  verify `init -> doctor -> explain -> plan` on a fresh runtime root without
  credentials or external infrastructure.
- Added [docs/first-run.md](docs/first-run.md) and updated runtime-root docs so
  onboarding starts from explicit root initialization and diagnostics before
  profile execution.
- Published `rexecop==0.2.12a0` on PyPI.

## [0.2.11a0] - 2026-06-28

- Added governed manual watchdog recovery records through
  `rexecop watchdog manual-record`. The command records signed
  `renew_lease`, `mark_stale` or `escalate_operator` decisions with bounded
  actor/scope context, GovEngine supervisor-action admission and SCLite
  `watchdog_decision.v0.1` truth artifacts; it does not execute recovery.

## [0.2.10a0] - 2026-06-28

- Added an opt-in domain-neutral worker watchdog slice. `worker run --watchdog`
  records bounded worker heartbeats and queue depth, moves stale inbox files to
  `.rexecop/dead_letter/` before execution, and dead-letters failed inbox files
  without copying trigger payloads into watchdog records.
- Extended the watchdog slice with bounded inbox retry budgets, stale active
  operation `block_autostart` records, GovEngine supervisor-action admission and
  SCLite `watchdog_decision.v0.1` truth artifacts.

## [0.2.9a0] - 2026-06-28

- Published the final R0 public stack baseline over `tecrax==0.3.8a0`
  after the Tecrax trigger/reaction profile line reached PyPI. This is a
  dependency/documentation truth patch; it does not add new runner behavior.

## [0.2.8a0] - 2026-06-28

- Added a cross-repo stack contract compatibility matrix and
  `scripts/validate_stack_contracts.py` gate for current package ranges,
  readiness labels and non-claims before later automation phases.
- Added a neutral `reaction-plan --operation` path that loads a profile-produced
  SCLite `reaction_observation` from a completed source operation instead of
  requiring a manually supplied observation file.
- Added neutral profile conformance checks and a CI/sign-off gate for
  profile-declared reaction-observation handoff contracts.
- Added opt-in `auto_react=plan_only` for completed read-only operations. When
  a profile-produced `reaction_observation` is present, RExecOp can create a
  replayable reaction plan and admitted child operation plan without starting
  the child operation.
- Preserved target catalog binding for reaction-planned child operations when
  the source operation was planned through a target catalog, including fail-closed
  catalog applicability/drift checks before child creation.
- Added a domain-neutral trigger event intake slice with profile-owned
  `triggers/trigger_rules.yaml`, deterministic trigger decisions, event digest
  binding, dedupe, cooldown, timestamp-skew fail-closed checks and file-inbox
  processing that creates operation plans without auto-starting them.
- Added neutral `target_from` and `catalog_target_from` trigger operation
  bindings so profiles can resolve operation targets from event fields without
  embedding domain semantics in RExecOp core.
- Bound trigger decisions to GovEngine `TriggerPlanningRequest` admission before
  creating operation plans; trigger decision artifacts now carry bounded request
  and admission digests.
- Raised the SCLite source dependency to `sclite-core==1.0.8` and projected
  trigger decisions into the SCLite `trigger_decision.v0.1` artifact
  shape with event/rule/admission digests and optional child-operation refs,
  while keeping trigger matching, policy and execution ownership outside SCLite.
- Published the trigger/reaction baseline over public `sclite-core==1.0.6`,
  `govengine==0.16.2`, and `tecrax==0.3.6a0` so clean installs no longer depend
  on local SCLite source checkouts for trigger-decision truth artifacts.

## [0.2.7a0] - 2026-06-27

- Added the PEP 561 `py.typed` marker so downstream stack profiles can type-check
  against RExecOp's exported modules instead of treating the package as untyped.
- Added `types-PyYAML` to the development extra so a clean `rexecop[dev]`
  environment can run `mypy src/rexecop` without manual stub installation.
- Added Tecrax Zabbix and Portainer HTTP action identity regression vectors that verify
  plan bindings, connector `action_contract_digest`, bounded receipt output digests and
  backend-not-called drift failures without adding domain semantics to RExecOp core.
- Updated Tecrax integration fixtures for the profile-owned bounded available-update
  summary action without adding host-update semantics to RExecOp core.
- Published the alpha line over `govengine>=0.16.1,<0.17`,
  `sclite-core>=1.0.5,<1.1`, and `tecrax>=0.3.6a0,<0.4` while preserving the
  existing B2/R4c execution and catalog boundary.

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

[0.2.14a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.13a0...HEAD
[0.2.13a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.12a0...v0.2.13a0
[0.2.12a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.11a0...v0.2.12a0
[0.2.11a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.10a0...v0.2.11a0
[0.2.10a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.9a0...3372bb3
[0.2.9a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.8a0...v0.2.9a0
[0.2.8a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.7a0...v0.2.8a0
[0.2.7a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.6a0...v0.2.7a0
[0.2.6a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.5a0...v0.2.6a0
[0.2.5a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.4a0...v0.2.5a0
[0.2.4a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.3a0...v0.2.4a0
[0.2.3a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.2a0...v0.2.3a0
[0.2.2a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.1a0...v0.2.2a0
[0.2.1a0]: https://github.com/rozmiarD/RExecOP/compare/v0.2.0a0...v0.2.1a0
[0.2.0a0]: https://github.com/rozmiarD/RExecOP/compare/v0.1.5a0...v0.2.0a0
[0.1.0a0]: https://github.com/rozmiarD/RExecOP/compare/f483bed...75eb006
