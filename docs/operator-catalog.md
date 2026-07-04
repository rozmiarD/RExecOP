# Operator target and operation catalog

The catalog gives an operator a bounded view of configured targets and the
profile-defined operations that are technically applicable to each target. It is
not an authorization system.

Ownership remains explicit:

- RExecOp owns the neutral loader, projection, CLI and drift checks;
- profiles own target kinds, capabilities, operation descriptions, validation and
  runbook references;
- the operator owns real target mappings and environment references outside Git;
- GovEngine owns every admission decision;
- SCLite binds the digest-only catalog projection in the execution contract.

## Target catalog

The private YAML document references existing environments rather than copying
connector configuration or credentials:

```yaml
target_catalog:
  version: "0.1"
  targets:
    - id: node-01
      target_kind: node
      profile_ref: profile_name
      environment_ref: /operator/private/environment.yaml
      environment_target: node-01
      capabilities: [readonly_api]
      connector_refs: [status_api]
      classification:
        criticality: low
```

Store the real catalog outside Git, owned by the operator and mode `0600`. The
catalog must not contain credentials. Environment connector secret values remain
behind `secret_ref` and `REXECOP_SECRETS_FILE`.

RExecOp validates duplicate aliases, unknown fields, profile/environment matching,
target kind, enabled connector references and inline secret patterns. CLI output
does not expose `environment_ref` or resolved profile paths.

## Operation projection

The operation list is compiled from the current profile intent files. It is never
maintained as a second list. Profile intent catalog metadata supplies:

- operator title and summary;
- supported target kinds;
- required capabilities;
- side-effect classification;
- validation and runbook references.

Required connectors are derived from the referenced workflow. The result is one
of:

- `unsupported_target_kind`;
- `missing_capability`;
- `missing_connector`;
- `admission_required` when all technical requirements are satisfied.

`admission_required` is deliberately not named `allowed`. Planning and execution
still pass through the normal GovEngine boundary.

## CLI

```bash
rexecop targets list --catalog /operator/private/targets.yaml
rexecop targets show node-01 --catalog /operator/private/targets.yaml

rexecop operations list --profile profile_name
rexecop operations explain observe_status --profile profile_name
rexecop operations list --catalog /operator/private/targets.yaml --target node-01
```

Plan directly from the catalog:

```bash
rexecop plan \
  --catalog /operator/private/targets.yaml \
  --intent observe_status \
  --target node-01 \
  --mode dry_run
```

Check the same catalog during first-run diagnostics:

```bash
rexecop doctor \
  --profile profile_name \
  --env /operator/private/environment.yaml \
  --catalog /operator/private/targets.yaml
```

The plan binds canonical digests for the catalog, target descriptor, operation
descriptor, profile snapshot and environment. RExecOp recomputes the binding
immediately before `start`. Any drift blocks connector execution and requires a
new plan.

The SCLite execution contract includes only the digest binding and bounded aliases.
Private catalog/environment paths stay in mode-`0600` local runtime state needed
for deterministic resume; they are not copied into evidence or the review bundle.
