# Environment contract

Environment YAML binds a profile to operator infrastructure: **targets**, **connectors**,
and **safety** policy. RExecOp validates operation targets at `plan` time.

## Target semantics

Targets live under `environment.targets` as a map of names to specifications.

| Kind | YAML shape | Meaning |
| --- | --- | --- |
| **group** | `type: group` + `members: [...]` | Logical target (for example `all_critical_vms`) expanding to member host ids |
| **host** | `type: host` or omitted `type` | Single declared host id |
| **member** | not a top-level key | A host id listed under a group's `members` — valid as `--target` but resolves to that single member |

### `all_critical_vms`

`all_critical_vms` is **not** a built-in magic string. It is a conventional **group name**
that profiles and runbooks use when the environment declares:

```yaml
targets:
  all_critical_vms:
    type: group
    members:
      - vm-zabbix-01
      - vm-pbs-01
```

Operations pass `--target all_critical_vms` to address the whole group. Connector and
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
- Persisted on the operation as `metadata.policy_pack` and `metadata.target_criticality`.
- Operation-level verdict → `plan.govengine_request_preview.policy_decision` for GovEngine admission compose.
- Connector-level evaluation runs in `CompositeConnectorRuntime.invoke()` before any backend (shell, SSH, `http_api`, plugins).

Example pack: [examples/policy/rexecop-connectors-default.yaml](../examples/policy/rexecop-connectors-default.yaml).

Lab environment with pack wired: [examples/environments/small-public-unit-proxmox.policy.example.yaml](../examples/environments/small-public-unit-proxmox.policy.example.yaml).
The base [small-public-unit-proxmox.example.yaml](../examples/environments/small-public-unit-proxmox.example.yaml) omits `policy_pack` so apply/mutation tests stay neutral.

See [execution-contract.md](execution-contract.md) and [govengine-integration.md](govengine-integration.md).

## Related

- [profile-contract.md](profile-contract.md)
- [connector-contract.md](connector-contract.md)
- [architecture.md](architecture.md)
