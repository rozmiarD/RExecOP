# CLI reference

Complete `rexecop` command reference for the current release candidate (`1.0.0rc2`).
Only commands listed by `CLI_CONTRACTS` carry the candidate `stable_v1` JSON
compatibility promise; all other command surfaces are explicitly alpha. Stable
JSON/error projections are designed to omit secret values, private connector
endpoints and raw backend payloads. Redaction is finite, so review output before
external sharing.

For onboarding without backend IO, start with [first-run.md](first-run.md). For
lifecycle semantics see [operation-lifecycle.md](operation-lifecycle.md).

## Global options

| Option | Env var | Default | Purpose |
| --- | --- | --- | --- |
| `--root PATH` | `REXECOP_ROOT` | `./.rexecop` | Runtime root for store, queue, locks, inbox |
| `--instance NAME` | `REXECOP_INSTANCE` | — | Named instance under `./.rexecop/instances/` |
| `--storage file\|sqlite` | `REXECOP_STORAGE` | `file` | Operation store backend |
| `--json` | — | off | Emit machine-readable JSON on supported commands (`init`, `doctor`, `env lint`, `profile lint`, `policy explain`, `operations explain`, `secrets doctor`, `plan --explain`, lifecycle commands) |
| `--format json\|table\|markdown` | — | `json` | Human or JSON output format where supported; `--json` overrides |
| `--quiet` | — | off | Suppress non-essential output on supported human formats |
| `--verbose` | — | off | Include extended diagnostic lines on supported human formats |
| `--no-color` | — | off | Disable colorized stderr on legacy error paths |

Most lifecycle, triage, queue and worker commands require a initialized runtime
root (`rexecop init`). Metadata commands (`env lint`, `profile lint`, `action`,
`secrets`, `profiles`, `connectors`, `capabilities`, `policy explain`, `governance controls`) work
without a store.

Every store-backed command checks `runtime_manifest.json` before opening the
configured backend. A missing, malformed or non-object manifest, an unsupported
manifest schema or runtime major, and a mismatch between the persisted and
configured storage backend fail closed. Only explicit `rexecop init` may create
a missing manifest, and only when the selected root is absent or is a strictly
empty real directory with no symbolic-link path components. The manifest is
read as a bounded 64 KiB regular file without following links; duplicate keys,
non-object JSON and invalid encoding fail closed. Rerunning `init` is idempotent
only for a compatible root with the same backend. Alpha roots and roots from
another major require a new root or a matching binary: this release does not
migrate, downgrade or convert runtime roots. These checks constrain current
binaries and do not make older binaries retroactively safe.

## Runtime readiness

| Command | Purpose |
| --- | --- |
| `version` | Print package version |
| `init [--guided]` | Create runtime root layout; no secrets or backend IO |
| `doctor [--profile] [--env] [--catalog]` | Read-only runtime-root manifest compatibility, storage, single-executor and mutation posture, plugin inventory, stack compatibility, `security_blockers` and optional operator inputs |

## Alpha examples

| Command | Purpose |
| --- | --- |
| `examples materialize --output NEW_DIR` | Copy the packaged `first-run-demo` v0.1.0 fixture to one new local directory; no runtime root, connector, admission, evidence, overwrite, merge or force behavior |

For stable-runtime qualification, set `REXECOP_DEPLOYMENT_POSTURE=stable` and
allowlist every reviewed in-process plugin with `REXECOP_PLUGIN_ALLOWLIST`. JSON
output always includes all `blockers` plus `security_blockers`, the subset caused
by mutation/plugin/network/input/stack security checks. Storage and executor
certification failures remain blockers but are not mislabeled as security review
findings. This runtime classification does not replace the independent release
security review.

## Environment and secrets

| Command | Purpose |
| --- | --- |
| `env lint --env PATH [--profile]` | Validate environment YAML and inline-secret hygiene |
| `secrets doctor --env PATH` and/or `--catalog PATH` | Environment-key collisions, missing refs, duplicate ref reuse, secrets-file policy, redaction self-test |
| `secrets suggest-ref --env PATH [--connector NAME]` | Suggest collision-checked `secret_ref` names from connector shape without reading stores |

See [secrets-operator.md](secrets-operator.md).

All product-owned YAML inputs used by environment, profile, catalog, action,
reaction, trigger and secrets commands share bounded structural validation.
Duplicate or non-string mapping keys, aliases/merge keys, multiple documents,
unsafe/explicit timestamp or binary tags, non-finite numbers and byte/depth/node
budget overflows fail before model construction or connector work. Supported
JSON error envelopes preserve their command/schema shape and report
`invalid_yaml_structure` with a constant redacted message. Runtime persistence
defensively reports `invalid_json_value` for an injected non-finite value before
writing an operation, plan or event.

## Profile developer surface

| Command | Purpose |
| --- | --- |
| `profile lint --profile PATH [--track readonly\|mutation\|all]` | Profile conformance for selected track |
| `profile manifest` | Extension manifest `v0.1` for profiles, plugins and resolvers |
| `profile harness --profile PATH [--env PATH]` | Workflow test harness (dry-run fixture, evidence, bundle, policy-blocked path) |
| `profiles list` | Registered `rexecop.profiles` entry points and compatibility |
| `profiles show PROFILE [--track readonly\|mutation\|all]` | Intents, tracks, developer-check and operator-metadata projection |
| `connectors list` | Built-in and plugin connector backends |
| `connectors show BACKEND` | One backend descriptor and plugin compatibility |
| `capabilities list` | Neutral runtime capabilities and their source |
| `contracts cli` | Machine-readable command schema, format, exit-code and redaction contract registry |

See [profile-developer-surface.md](profile-developer-surface.md).

## Action metadata (no backend I/O)

| Command | Purpose |
| --- | --- |
| `action list [--profile] [--env] [--catalog] [--target]` | List profile actions and redacted metadata |
| `action show INTENT [...]` | One action contract, required refs and backend constraints |
| `action preview INTENT [...]` | Redacted HTTP/shell/SSH effective-call previews and bounded-output policy |
| `action policy-preview INTENT --target ID [--mode MODE] [...]` | Optional GovEngine policy-impact simulation (digest-bound, redacted); not admission |
| `action validate --all\|--intent INTENT [...]` | Profile/env bindings, secret hygiene, duplicate refs, workflow contract |
| `action templates list` | Built-in readonly templates: `http.simple-get`, `shell.readonly-allowlist`, `ssh.readonly-allowlist` |
| `action configure INTENT --env PATH [--template ID] [--dry-run] [--write-patch PATH]` | Bounded dry-run patch operations; never mutates `--env` |
| `action diff INTENT --env PATH` | Profile contract vs environment binding diff with shape digests and configure hint |

Profile/env/catalog resolution matches `action list`. `action configure`
supports dry-run patch generation only; it does not edit the selected
environment file.

See [environment-contract.md](environment-contract.md) and
[profile-developer-surface.md](profile-developer-surface.md).

## Catalog and operation descriptors

| Command | Purpose |
| --- | --- |
| `targets list --catalog PATH` | Bounded target descriptors from a private catalog |
| `targets show TARGET --catalog PATH` | One bounded target descriptor |
| `operations list --profile PATH` | Profile-owned operation catalog |
| `operations list --catalog PATH --target TARGET` | Applicability for one catalog target |
| `operations explain INTENT --profile PATH` | Profile-owned operation descriptor (not admission) |
| `operations unavailable --catalog PATH --target TARGET [--intent]` | Operations not technically applicable to a target |

See [operator-catalog.md](operator-catalog.md).

## Inspection before execution

| Command | Purpose |
| --- | --- |
| `governance controls [--profile PATH] [--track readonly\|mutation]` | GovEngine typed-execution control catalog and optional profile-governance projection; not admission |
| `policy explain --intent ID --target ID [--profile] [--env] [--catalog] [--mode]` | GovEngine policy path for one operation-shaped request |
| `operation explain --operation ID` | Stored plan bindings, expected artifacts, safe next actions |
| `operation review --operation ID [--format json\|table\|markdown]` | Decision screen for a stored plan |
| `operation diff --operation ID [--format json\|table\|markdown]` | Stored plan bindings vs current profile/env/catalog |
| `operation truth-path --operation ID` | Digest-bound governance, runtime receipt and SCLite-reference projection without execution |
| `runbook show INTENT --profile PATH [--format json\|table\|markdown]` | Profile-owned runbook ref and bounded content |
| `receipt show OPERATION_ID` | Redacted receipt export and SCLite refs with descriptor digest checks |
| `evidence show OPERATION_ID` | Bounded, redacted internal evidence event summary |
| `chain summary OPERATION_ID` | Digest-linked operation, evidence, reaction and SCLite chain summary |
| `chain explain OPERATION_ID` | Operation truth-path plus verified reaction replay status |
| `support bundle OPERATION_ID --redacted` | Redacted diagnostic bundle combining receipt, evidence and chain projections |

Inspection commands require a runtime store and an existing operation id from `plan`.
Audit commands are projections only: SCLite remains the canonical
lifecycle/evidence contract and verification layer.

## CLI Contract Registry

`contracts cli` emits `rexecop.cli_contract_registry.v0.1`. It is a read-only
registry for the 23 candidate `stable_v1` operator-facing JSON surfaces and records command argv, stable
schema id, supported output formats, exit-code meanings, redaction and
bounded-output claims, and the authority boundary for each output.

The registry also includes:

- `command_groups`: current operator-facing groups such as audit inspection,
  operation inspection, runtime triage and profile developer surfaces;
- `format_matrix`: whether a command is JSON-only, JSON-only behind a `--json`
  flag, or has a `--format` option;
- `exit_code_matrix`: exit code meanings and the error schema for each command.

The registry does not execute commands and does not replace command-specific
tests. The complete stable/alpha command classification lives in
`rexecop.public_api.v1`; see [public-api.md](public-api.md).

## CLI Error Envelope

Every command in `contracts cli` emits `rexecop.cli_error.v0.1` on exit code `1`
for operator-facing validation, lookup and runtime failures. Exit code `0` keeps
the command-specific success schema from the registry.

The envelope contains:

- `error_class`: normalized class such as `validation_error`, `missing_artifact`
  or `runtime_failure`;
- `reason_code`: stable machine-readable reason;
- `message`: short redacted human-readable message;
- `command` / `argv`: the command surface that failed;
- `safe_next_actions`: bounded operator next steps;
- `details`: redacted diagnostic projection from the failing command when useful.

Execution-kernel failures use stable codes `unsafe_destination`,
`concurrency_conflict`, `lease_lost`, and `outcome_indeterminate`. Public
envelopes use bounded messages and never include traceback or raw unexpected
exception text. Retry classification uses the recorded `error_class`, not
exception-message parsing.

Exit-code policy for registry commands:

| Exit code | Meaning | Output |
| --- | --- | --- |
| `0` | Success | Command schema from `contracts cli` |
| `1` | Validation, lookup or runtime failure | `rexecop.cli_error.v0.1` |

Representative failure paths include missing operation lookups (`status`,
`operation explain`, `operation review`, `operation diff`), audit projection
failures (`receipt show`, `evidence show`, `chain summary`, `chain explain`, unredacted
`support bundle`), runtime triage failures (`runtime status --no-json`,
`dead-letter show`, `explain-error`), reaction lookup failures (`reaction explain`),
runtime blockers in `ops`, and failed
`profile lint` conformance. `runtime status` is JSON-only: use `--json` (default)
or `--no-json` triggers `unsupported_output_format`.

## Observability

| Command | Purpose |
| --- | --- |
| `observability logs list [--operation ID] [--correlation-id ID] [--limit N]` | Bounded structured logs with correlation and artifact refs (`rexecop.structured_log_event.v0.1`) |
| `observability diagnostics` | Runtime diagnostics using the same failure classes as `explain-error` (`rexecop.runtime_diagnostics.v0.1`) |

Structured logs record plan, admission, evidence, receipt and typed-execution
spec refs under `<root>/observability/events/`. Output is redacted and does not
expose raw secrets or private connector payloads.

## Runtime triage and recovery

| Command | Purpose |
| --- | --- |
| `runtime status [--json]` | Queue, active operations, locks and dead-letter summary |
| `ops` | Aggregate blockers, dead letters, stale locks and action-required items |
| `explain-error REF` | Failure class and safe next actions for operation/dead-letter/watchdog refs |
| `dead-letter list` | Watchdog dead-letter manifest |
| `dead-letter show ID` | One dead-letter item (redacted) |
| `locks list` | Advisory target locks and stale holders |
| `runtime recover [--json]` | Reconcile stale leases and receipt gaps after restart |
| `runtime reconstruct-status [--json]` | Read-only runtime-store reconstruction readiness, rules and blockers |
| `backup create [--output PATH]` | Secret-scanned runtime store tarball + manifest |
| `backup restore --archive PATH` | Restore from tarball manifest |
| `watchdog manual-record --action ... --reason ... --actor-ref ... --scope ...` | Governed manual watchdog decision without executing recovery |

See [runtime-recovery-ops.md](runtime-recovery-ops.md).

`backup create` requires a compatible initialized source root. `backup restore`
is the deliberate new-root exception: it validates the archived runtime
manifest before atomically promoting the new target. The restored manifest
retains its recorded backend, so later commands must select that same backend.
Restore does not perform runtime-root migration or backend conversion.

## Operation lifecycle

Requires initialized runtime root unless noted.

| Command | Purpose |
| --- | --- |
| `plan --catalog PATH --intent ID --target ID [--mode dry_run\|apply\|...]` | Create operation + `OperationPlan`; GovEngine gate for mutating modes |
| `plan --explain --profile PATH --env PATH --intent ID --target ID [--mode MODE]` | Profile + policy projection before operation creation (`rexecop.plan_explain.v0.1`); no store write |
| `approve --operation ID` | Manual approval after `approval_required` |
| `start --operation ID` | Execute workflow (queues when lock/capacity busy) |
| `pause --operation ID` | Pause at `pause_safe` workflow steps only |
| `resume --operation ID` | Resume from paused state |
| `cancel --operation ID` | Abort from exactly `waiting_for_approval`, `approved`, `running` or `paused` |
| `retry --operation ID` | Operator retry when profile policy allows |
| `rollback --operation ID` | Create/recover the persisted rollback child and start it only when independently admitted |
| `validate --operation ID` | Re-run declarative profile validation rules for one operation |
| `escalate --operation ID` | Build operator escalation package |
| `status --operation ID` | Current operation state |
| `history --operation ID` | Transition and evidence history |

`rollback` returns `rollback_operation_id`, `parent_operation_id`, mode, state/status, success,
bounded step results, error classification, `requires_approval`, and a safe `continuation` hint.
For an approval-required result, run `approve --operation <rollback_operation_id>` and then
`start --operation <rollback_operation_id>`; approval of the failed parent is not reused. The
command is idempotent for a parent: repeated calls recover the persisted child and never create a
second rollback. A failed `outcome_indeterminate` child requires reconciliation, not `retry`.

## Queue, worker and triggers

| Command | Purpose |
| --- | --- |
| `queue [--drain]` | Inspect FIFO run-now backlog; `--drain` processes one batch |
| `worker run [--once] [--poll-interval SEC] [--watch-inbox] [--watchdog] [--inbox-retry-budget N]` | Poll queue and start approved operations |
| `trigger` | Create operation from JSON stdin or `--profile --env --intent --target` flags |

`trigger` accepts webhook-style JSON on stdin and supports `trigger_event` payloads.
See [operator-scheduler-pattern.md](operator-scheduler-pattern.md).

## Deterministic reaction interpreter

| Command | Purpose |
| --- | --- |
| `reaction-plan --profile PATH --env PATH --target ID [--observation PATH] [--operation ID]` | Compile and evaluate one bounded profile-defined reaction |
| `reaction-start --reaction ID` | Start the admitted child operation for a reaction |
| `reaction-replay --reaction ID` | Replay a completed reaction chain |
| `reaction explain --reaction ID` | Explain persisted reaction artifacts and replay status without execution |
| `reaction-proposal-validate --proposal PATH` | Validate an LLM escalation proposal as non-executable input |
| `reaction-proposal-review --proposal PATH` | Review an advisory proposal without exposing raw explanation text |
| `reaction-proposal-submit --proposal PATH --decision accept_for_planning\|reject` | Record operator proposal handling without planning or execution |

See [reaction-interpreter.md](reaction-interpreter.md).

## Operator journey smoke

Automated subprocess smokes validate end-to-end CLI chains without private infrastructure:

| Script | Scope |
| --- | --- |
| `scripts/validate_first_run_smoke.py` | Onboarding on `first-run-demo` |
| `scripts/validate_operator_journeys.py` | Read-only execution, failure/triage/retry, governance and audit journeys on `runtime-fixture` |

See the
[standard read-only workflow](../OPERATOR_RUNBOOK.md#standard-read-only-workflow)
for the manual equivalents.

## Command groups (quick index)

```text
rexecop [--root] [--instance] [--storage]
  init | doctor | version
  examples materialize
  env lint
  secrets doctor | secrets suggest-ref
  profile lint | profile manifest | profile harness
  profiles list | profiles show
  connectors list | connectors show
  capabilities list
  action templates list
  action list | show | preview | policy-preview | validate | configure | diff
  targets list | show
  operations list | explain | unavailable
  governance controls
  policy explain
  operation explain | review | diff | truth-path
  runbook show
  receipt show | evidence show | chain summary | chain explain | support bundle
  runtime status | reconstruct-status | recover
  ops | explain-error
  observability logs list | diagnostics
  dead-letter list | show
  locks list
  backup create | restore
  watchdog manual-record
  plan | approve | start | pause | resume | cancel | retry | rollback
  validate | escalate | status | history
  queue [--drain]
  worker run
  trigger
  reaction-plan | reaction-start | reaction-replay | reaction explain
  reaction-proposal-validate | reaction-proposal-review | reaction-proposal-submit
```
