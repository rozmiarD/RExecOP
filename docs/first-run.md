# First Run

This path validates a local RExecOp runtime without external infrastructure,
credentials, or mutating connectors.

## 1. Materialize the bundled fixture

The packaged `v0.1.0` fixture is a byte-exact copy of the public
`examples/first-run-demo` source mirror. Choose a new local directory; this
does not initialize a runtime root, inspect a profile, run a connector, issue
admission, or emit evidence.

```bash
rexecop examples materialize --output /tmp/rexecop-first-run-demo
```

The JSON result lists the exact eight files and explicit non-claims. Existing
paths, symlink destinations, and symlink ancestors are rejected: there is no
overwrite, merge, force, adoption, migration, or upgrade behavior. The local
rename does not claim distributed/network-filesystem atomicity, power-loss
durability, or hostile shared-directory race safety.

## 2. Initialize a runtime root

```bash
rexecop --root /tmp/rexecop-first-run init --guided
```

Expected result: JSON with `status: initialized`, `secrets_created: false`, and
first-run `next_steps`.

## 3. Check the runtime and fixture inputs

```bash
rexecop --root /tmp/rexecop-first-run doctor \
  --profile /tmp/rexecop-first-run-demo/profile/profile.yaml \
  --env /tmp/rexecop-first-run-demo/environment.yaml \
  --catalog /tmp/rexecop-first-run-demo/catalog.yaml
```

Expected result: `status: passed`, no blockers, no warnings, and a passed
`mutation_posture` check reporting `stable_read_only` with `apply_enabled: false`.
The JSON report also contains an empty `security_blockers` list. Stable qualification
with installed plugins additionally requires `REXECOP_DEPLOYMENT_POSTURE=stable`
and an explicit `REXECOP_PLUGIN_ALLOWLIST`; the public first-run fixture remains a
no-I/O onboarding check, not independent security review evidence.

## 4. Lint the operator inputs

```bash
rexecop profile lint \
  --profile /tmp/rexecop-first-run-demo/profile/profile.yaml \
  --track readonly

rexecop env lint \
  --env /tmp/rexecop-first-run-demo/environment.yaml \
  --profile /tmp/rexecop-first-run-demo/profile/profile.yaml
```

Expected result: both commands return `status: passed`.

Optional developer-surface checks (no credentials required for the demo fixture):

```bash
rexecop profiles show /tmp/rexecop-first-run-demo/profile/profile.yaml --track readonly
rexecop operations unavailable \
  --catalog /tmp/rexecop-first-run-demo/catalog.yaml \
  --target fixture-target
rexecop connectors list
rexecop capabilities list
```

Expected result: `profiles show` reports readonly conformance passed;
`operations unavailable` returns an empty `unavailable` list when the target
matches the demo profile technically.

## 5. Explain and plan the demo operation

```bash
rexecop operations explain inspect \
  --profile /tmp/rexecop-first-run-demo/profile/profile.yaml

rexecop --root /tmp/rexecop-first-run plan \
  --catalog /tmp/rexecop-first-run-demo/catalog.yaml \
  --intent inspect \
  --target fixture-target \
  --mode dry_run
```

Expected result: `operations explain` shows a side-effect-free operation, and
`plan` returns an operation id.

## 6. Optional named instances

Use `--instance <name>` or `REXECOP_INSTANCE=<name>` when you want separate
runtime roots under `./.rexecop/instances/<name>` without passing absolute
paths. Explicit `--root` still wins.

```bash
rexecop --instance lab init
rexecop --instance lab doctor
```

Named instances are local runtime isolation only. They do not add multi-tenant
authorization, policy ownership, or secrets management.
