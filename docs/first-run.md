# First Run

This path validates a local RExecOp runtime without external infrastructure,
credentials, or mutating connectors.

## 1. Initialize a runtime root

```bash
rexecop --root /tmp/rexecop-first-run init --guided
```

Expected result: JSON with `status: initialized`, `secrets_created: false`, and
first-run `next_steps`.

## 2. Check the runtime and fixture inputs

```bash
rexecop --root /tmp/rexecop-first-run doctor \
  --profile examples/first-run-demo/profile/profile.yaml \
  --env examples/first-run-demo/environment.yaml \
  --catalog examples/first-run-demo/catalog.yaml
```

Expected result: `status: passed`, no blockers, and no warnings.

## 3. Lint the operator inputs

```bash
rexecop profile lint \
  --profile examples/first-run-demo/profile/profile.yaml \
  --track readonly

rexecop env lint \
  --env examples/first-run-demo/environment.yaml \
  --profile examples/first-run-demo/profile/profile.yaml
```

Expected result: both commands return `status: passed`.

## 4. Explain and plan the demo operation

```bash
rexecop operations explain inspect \
  --profile examples/first-run-demo/profile/profile.yaml

rexecop --root /tmp/rexecop-first-run plan \
  --catalog examples/first-run-demo/catalog.yaml \
  --intent inspect \
  --target fixture-target \
  --mode dry_run
```

Expected result: `operations explain` shows a side-effect-free operation, and
`plan` returns an operation id.

## 5. Optional named instances

Use `--instance <name>` or `REXECOP_INSTANCE=<name>` when you want separate
runtime roots under `./.rexecop/instances/<name>` without passing absolute
paths. Explicit `--root` still wins.

```bash
rexecop --instance lab init
rexecop --instance lab doctor
```

Named instances are local runtime isolation only. They do not add multi-tenant
authorization, policy ownership, or secrets management.
