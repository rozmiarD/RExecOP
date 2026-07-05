# Environment contract

Environment YAML binds a profile to operator infrastructure: **targets**, **connectors**,
and **safety** policy. RExecOp validates operation targets at `plan` time.

## Linting

Use `env lint` before planning against a new or edited environment:

```bash
rexecop env lint --env examples/first-run-demo/environment.yaml \
  --profile examples/first-run-demo/profile/profile.yaml
```

The command loads the environment, rejects inline secrets through the same
sanitization rules used by runtime paths, optionally checks that the environment
profile matches the supplied profile, and reports target, connector and
`secret_ref` counts as JSON.

## Secrets doctor

Use `secrets doctor` when the environment declares `secret_ref` fields and you
need to verify resolution and file policy **without printing secret values**:

```bash
rexecop secrets doctor --env /operator/private/environment.yaml
rexecop secrets doctor --env ./environment.yaml --secrets-file ~/.rexecop/secrets.yaml
```

`env lint` checks YAML hygiene; `secrets doctor` checks missing refs, duplicate
ref reuse, `REXECOP_SECRETS_FILE` permissions, orphan file keys, and a redaction
self-test. See [secrets-operator.md](secrets-operator.md).

## Target semantics

Targets live under `environment.targets` as a map of names to specifications.

| Kind | YAML shape | Meaning |
| --- | --- | --- |
| **group** | `type: group` + `members: [...]` | Logical target (for example `fixture-group`) expanding to member ids |
| **host** | `type: host` or omitted `type` | Single declared host id |
| **member** | not a top-level key | A host id listed under a group's `members` — valid as `--target` but resolves to that single member |

### Group targets

Group names are **not** built-in magic strings. They are conventional names that profiles and
runbooks use when the environment declares:

```yaml
targets:
  fixture-group:
    type: group
    members:
      - fixture-target
      - fixture-target-2
```

Operations pass `--target fixture-group` to address the whole group. Connector and
internal actions receive the logical target string; domain handlers may expand members
via `environment.resolve_targets`.

### Plan-time validation

`rexecop plan` rejects targets that are:

- empty;
- not a key in `environment.targets`;
- not a member of any declared `type: group`.

Helper: `rexecop.environment.targets.describe_target()` returns `kind`, `members`, and
optional `group` for member targets.

## Connectors

Each workflow `connector` step must reference a connector name present and **enabled**
in `environment.connectors`. Disabled or missing connectors fail at `plan` with
`RExecOpValidationError`.

When a profile connector declares `backend` and `command_shapes`, RExecOp also requires
the environment connector backend and allowlist to match the profile exactly. A changed
command, argv list, missing action, duplicate action, or undeclared capability fails at
plan time. Concrete command semantics remain owned by the profile, not RExecOp core.

Profiles can set `enforce_declared_modes: true` on an intent to reject any CLI mode not
listed by that intent. This is opt-in so older profiles retain their established behavior.

### Action metadata validation

Before planning, operators can inspect and validate profile/environment action
bindings without contacting backends:

```bash
rexecop action list --profile examples/first-run-demo/profile/profile.yaml \
  --env examples/first-run-demo/environment.yaml
rexecop action show inspect --profile examples/first-run-demo/profile/profile.yaml \
  --env examples/first-run-demo/environment.yaml
rexecop action preview inspect --profile examples/first-run-demo/profile/profile.yaml \
  --env examples/first-run-demo/environment.yaml
rexecop action configure inspect --profile examples/first-run-demo/profile/profile.yaml \
  --env examples/first-run-demo/environment.yaml --dry-run
rexecop action diff inspect --profile examples/first-run-demo/profile/profile.yaml \
  --env examples/first-run-demo/environment.yaml
rexecop action validate --all --profile examples/first-run-demo/profile/profile.yaml \
  --env examples/first-run-demo/environment.yaml
```

The action metadata commands report descriptor digests, connector backend
classes, shape digests when available, required secret refs, redacted
effective-call previews and catalog applicability. HTTP previews omit base URLs
and auth material; shell/SSH previews omit private endpoint and identity
configuration; fixture previews expose only data digests. They intentionally do
not print resolved secret values or connector configuration, do not perform
backend IO, and do not replace GovEngine admission or SCLite truth artifacts.

`action diff` compares profile connector contracts against the operator
environment for one intent: connector presence, backend class, action/allowlist
bindings and HTTP shape digests. It includes an advisory `configure_hint` with
the patch digest that `action configure --dry-run` would emit, without mutating
the environment file.

`action configure --dry-run` emits bounded patch operations for missing
profile-declared HTTP action entries or read-only shell/SSH allowlist entries.
It does not modify the environment file. `--write-patch <path>` writes only the
patch document for operator review; it is still not an apply operation.

## Safety block

`safety` carries runtime policy copied into `operation.metadata.runtime_policy`
(`max_concurrent_operations`, `target_lock_enabled`, `maintenance_windows`, …).

## Policy pack (`policy_pack`)

Optional declarative GovEngine policy for connector and operation admission:

```yaml
environment:
  policy_pack:
    policy_id: rexecop-connectors
    version: "2026-06-20"
    rules:
      - rule_id: allow-read-connectors
        effect: allow
        conditions:
          action.mode: read
```

- Compiled at `plan` via `PolicyCompiler`; invalid packs fail plan with `RExecOpValidationError`.
- Persisted on the operation as `metadata.policy_pack`, `metadata.policy_pack_lifecycle`
  and `metadata.target_criticality`.
- `metadata.policy_pack_lifecycle` uses schema `rexecop.policy_pack_lifecycle.v0.1`
  and records `absent` or `compiled` status, GovEngine-owned
  `policy_pack_digest`, lifecycle stages (`declared`, `compiled`,
  `bound_to_operation`, `enforcement_projected`) and enforcement plan/admission
  digests when present.
- Operation-level `allow` and `allow_with_obligations` are admitted only through
  GovEngine `PolicyEnforcementPlan` and existing `GovAdmissionDecision`. Supported controls are `receipt_required`,
  `output_digest_required`, `output_limit`, `timeout`, and `max_steps`; unknown or
  malformed controls fail plan.
- Accepted operation verdict → `plan.govengine_request_preview.policy_decision` for GovEngine admission compose.
- Connector-level evaluation runs in `CompositeConnectorRuntime.invoke()` before any
  backend and remains plain-allow-only; connector-specific obligations are fail-closed.

Example pack: [examples/policy/rexecop-connectors-default.yaml](../examples/policy/rexecop-connectors-default.yaml).

Lab environment with pack wired: [examples/environments/runtime-fixture.policy.example.yaml](../examples/environments/runtime-fixture.policy.example.yaml).
The base [runtime-fixture.example.yaml](../examples/environments/runtime-fixture.example.yaml) omits `policy_pack` so apply/mutation tests stay neutral.

See [execution-contract.md](execution-contract.md) and [govengine-integration.md](govengine-integration.md).

## Related

- [profile-contract.md](profile-contract.md)
- [connector-contract.md](connector-contract.md)
- [architecture.md](architecture.md)
