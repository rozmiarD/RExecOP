# Profile contract

RExecOp loads **domain profiles** from the filesystem or from registered Python packages.
Profiles define intents, workflows, connector capability contracts, and declarative validation
rules. RExecOp core must not embed Tecrax or other domain semantics.

## Resolution

`--profile` accepts either:

1. **Path** — `profile.yaml` or a profile directory containing `profile.yaml`
2. **Registered name** — via `rexecop.profiles` entry points (e.g. `tecrax`)

```bash
rexecop plan --profile tecrax --env ... --intent check_backup_status ...
rexecop plan --profile examples/profiles/tecrax-fixture/profile.yaml ...
```

Resolver: `rexecop.profile.resolver.resolve_profile_path()`.

## External Tecrax package

Production Tecrax semantics ship in [`tecrax`](https://github.com/rozmiarD/tecrax):

```bash
pip install -e /path/to/rexecop -e /path/to/tecrax
```

Entry point:

```toml
[project.entry-points."rexecop.profiles"]
tecrax = "tecrax:profile_root"
```

Domain packages may also register:

```toml
[project.entry-points."rexecop.internal_actions"]
tecrax = "tecrax.internal_actions:register_handlers"

[project.entry-points."rexecop.connector_backends"]
tecrax_fixture = "tecrax.fixture.mock_runtime:build_runtime"
```

RExecOp core must **never** import `tecrax` or `tecrax_profile`. CI enforces this with a grep guard on
`src/rexecop`.

## Internal actions

Workflow steps with `type: internal` resolve handlers from:

1. Built-in core handlers (`record_rollback_marker`)
2. `rexecop.internal_actions` entry points (e.g. `tecrax`)

Missing handlers fail with `internal_action_not_registered:<action>`.

## Connector fixtures

Mock connector backends (`mode: mock`) are generic unless environment YAML sets `fixture:` to a
registered `rexecop.connector_backends` name (e.g. `tecrax_fixture`).

## Profile layout

```text
profile/
  profile.yaml                 # profile_contract metadata
  intents/<intent>.yaml
  workflows/<workflow>.yaml
  connectors/<name>.yaml       # capability contract (used by http_api gating)
  validation_rules/<intent>.yaml
```

## profile_contract sections

Validated by `rexecop.profile.contract.validate_profile_contract()`:

`intents`, `workflows`, `connector_requirements`, `risk_classes`, `evidence_requirements`,
`governance_expectations`, `validation_rules`, `escalation_rules` (required);
`rollback_rules` (optional).

## Validation rules

Success criteria are **declarative YAML** under `validation_rules/`. RExecOp evaluates generic
step types:

| Step type | Behavior |
| --- | --- |
| `require_mapping` | Shared-state key must be a mapping |
| `require_truthy_path` | Dot-path must be truthy |
| `require_equals` | Dot-path must equal expected value |

Domain meaning stays in the profile package — not in `src/rexecop/validation/validator.py`.

## Fixture vs product profile

| Location | Purpose |
| --- | --- |
| `examples/profiles/tecrax-fixture/` | Bootstrap/offline tests in rexecop repo (requires `tecrax` for mock + internals) |
| `examples/profiles/http-health-fixture/` | http_api-only golden path without domain internals |
| `tecrax` package | Operator-facing Tecrax profile |

## Out of scope

Ravenclaw is **legacy** and not a RExecOp target. No Ravenclaw profile path is planned in core.
