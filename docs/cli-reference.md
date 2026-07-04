# CLI reference

Complete `rexecop` command reference for the current source line (`0.2.14a0`).
Commands emit stable JSON unless noted. Secret values, connector endpoints and raw
backend payloads are never printed.

For onboarding without backend IO, start with [first-run.md](first-run.md). For
lifecycle semantics see [operation-lifecycle.md](operation-lifecycle.md).

## Global options

| Option | Env var | Default | Purpose |
| --- | --- | --- | --- |
| `--root PATH` | `REXECOP_ROOT` | `./.rexecop` | Runtime root for store, queue, locks, inbox |
| `--instance NAME` | `REXECOP_INSTANCE` | — | Named instance under `./.rexecop/instances/` |
| `--storage file\|sqlite` | `REXECOP_STORAGE` | `file` | Operation store backend |

Most lifecycle, triage, queue and worker commands require a initialized runtime
root (`rexecop init`). Metadata commands (`env lint`, `profile lint`, `action`,
`secrets`, `profiles`, `connectors`, `capabilities`, `policy explain`) work
without a store.

## Runtime readiness

| Command | Purpose |
| --- | --- |
| `version` | Print package version |
| `init [--guided]` | Create runtime root layout; no secrets or backend IO |
| `doctor [--profile] [--env] [--catalog]` | Runtime root, storage, stack compatibility and optional operator inputs |

## Environment and secrets

| Command | Purpose |
| --- | --- |
| `env lint --env PATH [--profile]` | Validate environment YAML and inline-secret hygiene |
| `secrets doctor --env PATH` and/or `--catalog PATH` | Missing refs, duplicate ref reuse, secrets-file policy, redaction self-test |
| `secrets suggest-ref --env PATH [--connector NAME]` | Suggest `secret_ref` names from connector shape without reading stores |

See [secrets-operator.md](secrets-operator.md).

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

## Action metadata (M5, no backend IO)

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

Profile/env/catalog resolution matches `action list`. `action configure` supports
`--dry-run` only in M5.

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
| `policy explain --intent ID --target ID [--profile] [--env] [--catalog] [--mode]` | GovEngine policy path for one operation-shaped request |
| `operation explain --operation ID` | Stored plan bindings, expected artifacts, safe next actions |
| `operation review --operation ID [--format json\|table\|markdown]` | Decision screen for a stored plan |
| `operation diff --operation ID [--format json\|table\|markdown]` | Stored plan bindings vs current profile/env/catalog |
| `runbook show INTENT --profile PATH [--format json\|table\|markdown]` | Profile-owned runbook ref and bounded content |
| `receipt show OPERATION_ID` | Redacted receipt export and SCLite refs with descriptor digest checks |
| `evidence show OPERATION_ID` | Bounded, redacted internal evidence event summary |
| `chain summary OPERATION_ID` | Digest-linked operation, evidence, reaction and SCLite chain summary |
| `support bundle OPERATION_ID --redacted` | Redacted diagnostic bundle combining receipt, evidence and chain projections |

Inspection commands require a runtime store and an existing operation id from `plan`.
Audit commands are projections only: SCLite remains the authoritative truth layer.

## CLI Contract Registry

`contracts cli` emits `rexecop.cli_contract_registry.v0.1`. It is a read-only
registry for operator-facing JSON surfaces and records command argv, stable
schema id, supported output formats, exit-code meanings, redaction and
bounded-output claims, and the authority boundary for each output.

The registry does not execute commands and does not replace command-specific
tests. It is the M8 anti-drift surface for release-closure checks.

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
| `backup create [--output PATH]` | Secret-scanned runtime store tarball + manifest |
| `backup restore --archive PATH` | Restore from tarball manifest |
| `watchdog manual-record --action ... --reason ... --actor-ref ... --scope ...` | Governed manual watchdog decision without executing recovery |

See [runtime-recovery-ops.md](runtime-recovery-ops.md).

## Operation lifecycle

Requires initialized runtime root unless noted.

| Command | Purpose |
| --- | --- |
| `plan --catalog PATH --intent ID --target ID [--mode dry_run\|apply\|...]` | Create operation + `OperationPlan`; GovEngine gate for mutating modes |
| `approve --operation ID` | Manual approval after `approval_required` |
| `start --operation ID` | Execute workflow (queues when lock/capacity busy) |
| `pause --operation ID` | Pause at `pause_safe` workflow steps only |
| `resume --operation ID` | Resume from paused state |
| `cancel --operation ID` | Abort before completion |
| `retry --operation ID` | Operator retry when profile policy allows |
| `rollback --operation ID` | Run explicit workflow rollback steps after failure |
| `validate --operation ID` | Re-run declarative profile validation rules for one operation |
| `escalate --operation ID` | Build operator escalation package |
| `status --operation ID` | Current operation state |
| `history --operation ID` | Transition and evidence history |

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
| `reaction-proposal-validate --proposal PATH` | Validate an LLM escalation proposal as non-executable input |

See [reaction-interpreter.md](reaction-interpreter.md).

## Command groups (quick index)

```text
rexecop [--root] [--instance] [--storage]
  init | doctor | version
  env lint
  secrets doctor | secrets suggest-ref
  profile lint | profile manifest | profile harness
  profiles list | profiles show
  connectors list | connectors show
  capabilities list
  action templates list
  action list | show | preview | validate | configure | diff
  targets list | show
  operations list | explain | unavailable
  policy explain
  operation explain | review | diff
  runbook show
  runtime status | recover
  ops | explain-error
  dead-letter list | show
  locks list
  backup create | restore
  watchdog manual-record
  plan | approve | start | pause | resume | cancel | retry | rollback
  validate | escalate | status | history
  queue [--drain]
  worker run
  trigger
  reaction-plan | reaction-start | reaction-replay | reaction-proposal-validate
```
