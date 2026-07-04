# Profile developer surface

RExecOp exposes a neutral discoverability layer for profiles, connector backends,
internal actions and runtime capabilities. These commands are **metadata and
compatibility tooling** — they do not execute connectors, resolve secret values,
or replace GovEngine admission.

## Developer journey

Typical profile-author flow before operator runs:

```text
profile lint --track readonly
  -> profiles show (intents, tracks, developer_check)
  -> profile harness --profile <profile>   # when a fixture environment is available
  -> secrets doctor --env <environment.yaml>
  -> action list/show/preview/configure/validate --profile <profile> --env <environment.yaml>
  -> operations unavailable --catalog <targets.yaml> --target <id>   # when using a catalog
  -> plan / operation review
```

`run_profile_developer_check()` (surfaced in `profiles show`) runs conformance,
plugin compatibility, GovEngine G3 `govengine_governance` compatibility and
the profile workflow test harness when a fixture environment is configured
**without** requiring a pre-initialized operator runtime store.

## Profile discoverability

```bash
rexecop profiles list
rexecop profiles show tecrax
rexecop profiles show examples/profiles/runtime-fixture/profile.yaml --track readonly
```

`profiles list` summarizes registered `rexecop.profiles` entry points with
readonly/mutation compatibility status.

`profiles show` returns:

- profile summary: version, intents, required capabilities, per-track conformance;
- `developer_check`: conformance + `plugin_compatibility` + `govengine_governance` + `workflow_harness`;
- `operator_metadata`: coverage status for profile-owned `operator_metadata.yaml`;
- bounded `extension_manifest` slice (`required_contracts`, `supported_tracks`).

JSON schema: `rexecop.profile_show.v0.1`.

## Profile workflow test harness

Profiles that ship the domain-neutral `runtime_fixture` example can run the M4
workflow test harness without backend IO:

```bash
rexecop profile harness --profile examples/profiles/runtime-fixture/profile.yaml
```

`run_profile_workflow_harness()` returns `rexecop.profile_workflow_harness.v0.1`
with four checks:

| Check | Meaning |
| --- | --- |
| `dry_run_fixture` | Read-only workflow completes in `dry_run` against `static_fixture` |
| `no_secret_evidence` | Evidence events stay redacted and free of strong secret patterns |
| `sclite_bundle_shape` | Exported receipt bundle passes `review_bundle` and required sidecars |
| `policy_blocked_path` | Mutating plan is fail-closed when policy denies the workflow |

Registered profiles without a bundled fixture environment report
`workflow_harness.status=skipped` in `profiles show` / `developer_check`.

## Profile-owned operator metadata

Profiles may ship `operator_metadata.yaml` beside `profile.yaml`. Tecrax owns
user-facing labels, runbook hints, safe next options and failure mapping; RExecOp
loads and projects the document without interpreting domain semantics.

```bash
rexecop operations explain diagnose_monitoring_host --profile tecrax
```

`operations explain` returns `rexecop.operation_profile_explain.v0.1` with the
catalog descriptor plus optional `operator_metadata` projection. The same
metadata enriches `operations unavailable`, `operation review`, and
`explain-error` when a profile root is available.

## Conformance categories

`profile lint` and conformance results include categorized checks:

| Category | Meaning |
| --- | --- |
| `readonly` | Read-only mode and side-effect class checks |
| `mutation` | Mutation-candidate contract checks |
| `reaction` | Reaction observation declaration and reaction pack |
| `catalog` | Operation catalog projection and intent metadata |
| `connector` | Workflow connector contracts |
| `validation` | Validation rule file presence and profile-local paths |

Tracks remain `readonly`, `mutation` or `all`. Categories are orthogonal to tracks.

## Extension manifest

```bash
rexecop profile manifest
```

Emits `rexecop.extension_manifest.v0.1` with:

- `compatibility_version` (current rexecop version);
- `required_contracts` (`profile_contract`, `connector_contract`, SCLite schema refs);
- `supported_tracks` (`readonly`, `mutation`, `all`);
- registered `profiles`, `connector_backends`, `internal_actions`, `secret_resolvers`;
- canonical `digest` of the manifest payload.

Use this when authoring or certifying profile/plugin packages against the
current rexecop host line.

## Connector discoverability

```bash
rexecop connectors list
rexecop connectors show http_api
```

Built-in backends include `mock`, `http_api`, `local_shell_readonly`,
`ssh_readonly`, and `static_fixture`. Plugin backends registered through
`rexecop.connector_backends` appear with `certification_tier: plugin`.

Each descriptor reports `supported_modes`, neutral `capability_descriptors`, and
`compatibility_version`. See [connector-contract.md](connector-contract.md).

## Capabilities

```bash
rexecop capabilities list
```

Lists neutral runtime capabilities and their source (`rexecop.core`,
`rexecop.connector_backends`, `rexecop.internal_actions`, secret resolver
primitives). Profile-declared capability names in intent catalog metadata are
separate from this runtime registry.

## Action metadata

```bash
rexecop action list --profile examples/first-run-demo/profile/profile.yaml \
  --env examples/first-run-demo/environment.yaml
rexecop action show inspect --profile examples/first-run-demo/profile/profile.yaml \
  --env examples/first-run-demo/environment.yaml
rexecop action preview inspect --profile examples/first-run-demo/profile/profile.yaml \
  --env examples/first-run-demo/environment.yaml
rexecop action configure inspect --profile examples/first-run-demo/profile/profile.yaml \
  --env examples/first-run-demo/environment.yaml --dry-run
rexecop action validate --all --catalog examples/first-run-demo/catalog.yaml \
  --target fixture-target
```

`action list`, `action show`, `action preview`, `action configure`, and
`action validate` are read-only metadata commands for profile authors and
operators. They compile profile-owned action descriptors, connector workflow
steps, backend classes, shape digests when available, required secret refs and
catalog applicability. Output uses stable schemas (`rexecop.action_list.v0.1`,
`rexecop.action_show.v0.1`, `rexecop.action_preview.v0.1`,
`rexecop.action_configure.v0.1`, `rexecop.action_validate.v0.1`) and reports
source digests instead of local operator file paths.

`action preview` renders redacted effective-call previews for `http_api`,
`local_shell_readonly`, `ssh_readonly`, and `static_fixture`. HTTP previews show
method, path template/preview, query keys, body shape, auth header name and
bounded response policy, but never base URLs, auth refs, auth prefixes or
resolved headers. Shell/SSH previews show the allowlisted command argv and
output limits, but never SSH host, user, port, identity file refs or resolved
identity paths. Static fixture previews expose only fixture data digests.

These commands do not execute connector backends, create execution requests,
request or imply GovEngine admission, emit SCLite truth artifacts, or print
resolved secrets / connector configuration.

`action configure --dry-run` generates bounded patch operations for profile
declared `http_api` action shapes and read-only shell/SSH `command_shapes`.
It never edits the environment YAML. `--write-patch <path>` writes only the
bounded patch document (`rexecop.action_configure_patch.v0.1`) so an operator
can review/apply it deliberately.

## Plugin compatibility report

`build_plugin_compatibility_report()` (used by `profiles show` and developer
checks) verifies that registered `rexecop.connector_backends` factories return a
valid runtime and that `rexecop.internal_actions` entry points load. Failures are
bounded JSON errors without backend IO.

## Authority boundaries

| Surface | Owns | Does not own |
| --- | --- | --- |
| `profiles *` | Profile metadata, conformance, plugin registration | Domain semantics, policy verdicts |
| `connectors *` | Backend descriptors and certification tier | Connector execution |
| `capabilities list` | Neutral capability registry | Target catalog capabilities |
| `action list/show/preview/configure/validate` | Action metadata, shape digests, redacted call preview, patch ops and env binding checks | Backend execution, GovEngine admission, SCLite truth |
| `profile manifest` | Host extension contract | Profile content |
| `operations unavailable` | Technical applicability reasoning | GovEngine admission |
