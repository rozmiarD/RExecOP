# Profile contract

RExecOp loads **domain profiles** from the filesystem or from registered Python packages.
Profiles define intents, workflows, connector capability contracts, and declarative validation
rules. RExecOp core must not embed Tecrax or other domain semantics.

## Resolution

`--profile` accepts either:

1. **Path** — `profile.yaml` or a profile directory containing `profile.yaml`
2. **Registered name** — via `rexecop.profiles` entry points (e.g. `tecrax`)

```bash
rexecop plan --profile tecrax --env ... --intent collect_basic_host_inventory ...
rexecop plan --profile examples/profiles/runtime-fixture/profile.yaml ...
```

Resolver: `rexecop.profile.resolver.resolve_profile_path()`.

## Linting

Use `profile lint` before using a new or changed profile in operator runs:

```bash
rexecop profile lint --profile examples/first-run-demo/profile/profile.yaml --track readonly
rexecop profile lint --profile <profile.yaml> --track mutation
rexecop profile lint --profile <profile.yaml> --track all
```

`readonly` verifies side-effect-free operation/catalog/workflow surfaces without
requiring mutation candidates to pass. `mutation` validates bounded mutating
candidates as candidates only; it is not an authorization or `mutation_ready`
claim. `all` checks all profile operations.

Lint output includes conformance **categories**: `readonly`, `mutation`,
`reaction`, `catalog`, `connector`, and `validation`. Each category reports
`status`, `errors`, and `warnings` independently of the overall track result.

## Developer surface

Profile authors can inspect registered profiles, extension contracts and plugin
compatibility without a runtime store:

```bash
rexecop profiles list
rexecop profiles show tecrax --track readonly
rexecop profile manifest
```

See [profile-developer-surface.md](profile-developer-surface.md) for
`connectors list/show`, `capabilities list`, extension manifest fields, and the
recommended developer journey.

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

Domain packages may also register internal handlers:

```toml
[project.entry-points."rexecop.internal_actions"]
tecrax = "tecrax.internal_actions:register_handlers"
```

Connector backend plugins are supported through `rexecop.connector_backends`, but RExecOp
does not ship or document domain-specific backend names as core behavior. Prefer the
built-in generic backends (`http_api`, `ssh_readonly`, `local_shell_readonly`,
`static_fixture`) unless a profile package explicitly owns an extension.

Plugins are **trusted in-process code**, not sandboxed workloads. Connector factories must
implement `rexecop.connector_backend_factory.v1`; internal registrars implement
`rexecop.internal_action_registrar.v1`. Plugin names cannot replace built-ins, and factory
`TypeError` is a plugin failure rather than a signal to retry a legacy zero-argument call.
Stable deployment requires every installed plugin entry-point name in
`REXECOP_PLUGIN_ALLOWLIST`; `rexecop doctor` reports and enforces that inventory.

Environment YAML may reference a backend name directly:

```yaml
connectors:
  fixture_source:
    enabled: true
    backend: http_api
    base_url_secret_ref: fixture_base_url
```

Domain API semantics belong in profile connector YAML and operator environment files, not in
`src/rexecop`.

Connector contracts that participate in typed execution declare GovEngine
operation requirements independently of the backend inventory:

```yaml
connector:
  name: fixture_source
  capabilities:
    - read_fixture_state
  required_capability_descriptors:
    - connector.fixture.static
```

`required_capability_descriptors` must be a duplicate-free list of non-empty
strings. It describes what the operation requires; the backend capability
descriptor separately describes what the runtime offers. RExecOp does not fill
missing requirements from backend declarations.

Profiles that deliberately support more than one backend can use
`required_capability_descriptors_by_backend`; an entry may be a list or an
action-to-list mapping. The generic list remains the fallback. Outbound
connectors also declare an independent `network_policy_binding` (or
`network_policy_binding_by_backend`) with allowed egress, schemes and address
classes. These fields belong to the profile contract and are never inferred
from the environment's requested destination.

RExecOp core must **never** import `tecrax` or `tecrax_profile`. CI enforces this with a grep
guard on `src/rexecop`.

## Internal actions

Workflow steps with `type: internal` resolve handlers from:

1. Built-in core handlers (`record_rollback_marker`)
2. `rexecop.internal_actions` entry points (e.g. `tecrax`)

Missing handlers fail with `internal_action_not_registered:<action>`.

## Connector fixtures

`examples/profiles/runtime-fixture/` uses the built-in `static_fixture` backend for deterministic
no-I/O lifecycle tests. Generic `mock` remains available for simple connector unit tests, but
domain fixture behavior belongs in the owning profile package.

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
| `examples/first-run-demo/` | Public-safe onboarding fixture for `init -> doctor -> explain -> plan` |
| `examples/profiles/runtime-fixture/` | Bootstrap/offline tests in rexecop repo; domain-neutral no-I/O fixture |
| `examples/profiles/http-health-fixture/` | http_api-only golden path without domain internals |
| `tecrax` package | Operator-facing Tecrax profile |

## Out of scope

Ravenclaw is **legacy** and not a RExecOp target. No Ravenclaw profile path is planned in core.
