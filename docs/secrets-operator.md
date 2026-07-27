# Secrets operator surface

RExecOp requires secret references instead of inline values in profile and
environment documents. Supported validators and redaction boundaries prohibit
resolved values from runtime evidence and CLI JSON projections. Operators
resolve values through `REXECOP_SECRET_<REF>` environment variables and/or
`REXECOP_SECRETS_FILE`.

These controls are defense in depth, not a claim that every arbitrary plugin,
host error or deliberately allowlisted field is leak-proof. Treat the runtime
root and process environment as sensitive.

## Resolution order

`ChainedSecretResolver` tries, in order:

1. `REXECOP_SECRET_<REF>` — ref normalized to uppercase with `-` replaced by `_`;
2. `REXECOP_SECRETS_FILE` — operator-managed YAML with a top-level `secrets:` mapping.

The environment-variable mapping is retained for compatibility, including
hyphenated refs. Distinct trimmed refs that project to the same key are rejected:
for example, `foo-bar` and `foo_bar` both project to
`REXECOP_SECRET_FOO_BAR`. An `EnvSecretResolver` claims that key for the first
ref before lookup; a later distinct collision is terminal and never falls
through to the file resolver. Reusing the same trimmed ref is allowed. File
resolution remains exact-key, so a directly used `FileSecretResolver` may keep
separate `foo-bar` and `foo_bar` entries.

See [connector-contract.md](connector-contract.md#secrets-port) for connector
`secret_ref` / `base_url_secret_ref` fields.

## Linting vs doctor

| Command | When | Scope |
| --- | --- | --- |
| `env lint` | Before planning with a new/edited environment | Inline secret hygiene, optional profile match, `secret_ref` counts |
| `secrets doctor` | Before staging/real connector runs | Environment-key collisions, ref resolution, file policy, duplicates, redaction self-test |
| `secrets suggest-ref` | While drafting connector config | Reference names/paths only; no value lookup |

`env lint` does **not** verify that secret values exist. `secrets doctor` does.
`secrets suggest-ref` does not verify resolution; it only suggests bounded ref
names such as `<connector>_base_url`, `<connector>_api_token` or
`<connector>_identity_file` from connector backend shape.

## secrets doctor

```bash
rexecop secrets doctor --env /operator/private/environment.yaml
rexecop secrets doctor --env ./env.yaml --catalog ./targets.yaml
rexecop secrets doctor --env ./env.yaml --secrets-file ~/.rexecop/secrets.yaml
```

Requires `--env` and/or `--catalog`. Exit code `1` when `status: blocker`.

JSON schema: `rexecop.secrets_doctor.v0.1`.

### Checks

| Check id | Blocker / warning | Meaning |
| --- | --- | --- |
| `inline_secrets` | blocker | Inline secret-like keys or strong secret patterns in YAML |
| `secret_ref_bindings` | blocker | Empty `secret_ref` / `*_secret_ref` fields |
| `secret_ref_env_collision` | blocker | Distinct refs project to the same legacy `REXECOP_SECRET_*` key |
| `missing_refs` | blocker | Declared ref not found in env or secrets file |
| `duplicate_refs` | warning | Same ref name reused across multiple bindings |
| `secrets_file_permissions` | blocker / warning | File ownership, mode `0600`, symlink, size limits |
| `orphan_file_refs` | warning | Keys in secrets file not referenced by inspected documents |
| `redaction_self_test` | blocker | Process-local redaction removes probe material |

The command's supported output contract omits resolved secret values. Error
messages from malformed secret files are bounded and avoid echoing file
content.

Collision checks run before missing-ref checks and include environments reached
through a strictly parsed target catalog. Repeated catalog targets referencing
the same physical environment document are inspected once. Normal environment
and catalog loads reject collisions directly; doctor uses the same parser/model
path in a private inspection mode that skips only collision enforcement so it
can return the dedicated blocker.

## secrets suggest-ref

```bash
rexecop secrets suggest-ref --env /operator/private/environment.yaml
rexecop secrets suggest-ref --env ./env.yaml --connector zabbix
```

Returns `rexecop.secrets_suggest_ref.v0.1` with existing refs and suggested
reference names for `http_api` and `ssh_readonly` connectors. It does not read
`REXECOP_SECRETS_FILE`, does not read `REXECOP_SECRET_*`, does not validate
resolution and does not print values.

Generated names retain their lowercase snake-case form. If the combined
existing and generated identities contain an exact existing/generated overlap,
a generated duplicate, or a legacy environment-key ambiguity, the command
fails and requires explicit refs; it does not add suffixes or hashes.

### Safe operator posture

- Keep `REXECOP_SECRETS_FILE` mode `0600`, owned by the runtime user, outside git.
- Prefer `secret_ref` over inline values in every environment and catalog document.
- Use `secrets suggest-ref` for reference naming, then store values out of band.
- Run `secrets doctor` after editing environment YAML or the secrets file, before
  `plan` or staging connector tests.

Environment-key ownership is thread-safe within one `EnvSecretResolver`
instance. It does not coordinate claims globally or across separately
constructed resolver instances.
