# Profile contract

RExecOp loads **domain profiles** from the filesystem or from registered Python
packages. Profiles define intents, workflows, connector requirements, and
validation rules. RExecOp core must not embed Tecrax, Ravenclaw, or other domain
semantics.

## Resolution

`--profile` accepts either:

1. **Path** — `profile.yaml` or a profile directory containing `profile.yaml`
2. **Registered name** — via `rexecop.profiles` entry points (e.g. `tecrax`)

```bash
rexecop plan --profile tecrax --env ... --intent check_backup_status ...
rexecop plan --profile examples/profiles/tecrax-fixture/profile.yaml ...
```

## External packages

Production Tecrax semantics ship in the separate **`tecrax-profile`** package:

```bash
pip install -e /path/to/rexecop -e /path/to/tecrax-profile
```

Entry point:

```toml
[project.entry-points."rexecop.profiles"]
tecrax = "tecrax_profile:profile_root"
```

RExecOp core must **never** import `tecrax_profile`. CI enforces this boundary.

## Profile layout

```
profile/
  profile.yaml              # profile_contract sections
  intents/<intent>.yaml
  workflows/<workflow>.yaml
  connectors/<name>.yaml
  validation_rules/<intent>.yaml
```

## Validation rules

Success criteria are **declarative YAML** under `validation_rules/`. RExecOp
evaluates generic step types (`require_mapping`, `require_truthy_path`,
`require_equals`) — domain meaning stays in the profile package.

## Fixture vs product profile

| Location | Purpose |
| --- | --- |
| `examples/profiles/tecrax-fixture/` | Bootstrap/offline tests in rexecop repo |
| `tecrax-profile` package | Real Tecrax profile for operators |

Ravenclaw is **legacy / out of scope** for RExecOp.
