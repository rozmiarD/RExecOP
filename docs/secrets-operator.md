# Secrets operator surface

RExecOp never stores secret values in git, runtime evidence, or CLI JSON output.
Operators declare **references** in environment YAML and resolve values through
`REXECOP_SECRET_<REF>` environment variables and/or `REXECOP_SECRETS_FILE`.

## Resolution order

`ChainedSecretResolver` tries, in order:

1. `REXECOP_SECRET_<REF>` — ref normalized to uppercase with `-` replaced by `_`;
2. `REXECOP_SECRETS_FILE` — operator-managed YAML with a top-level `secrets:` mapping.

See [connector-contract.md](connector-contract.md#secrets-port) for connector
`secret_ref` / `base_url_secret_ref` fields.

## Linting vs doctor

| Command | When | Scope |
| --- | --- | --- |
| `env lint` | Before planning with a new/edited environment | Inline secret hygiene, optional profile match, `secret_ref` counts |
| `secrets doctor` | Before staging/real connector runs | Ref resolution, file policy, duplicates, redaction self-test |
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
| `missing_refs` | blocker | Declared ref not found in env or secrets file |
| `duplicate_refs` | warning | Same ref name reused across multiple bindings |
| `secrets_file_permissions` | blocker / warning | File ownership, mode `0600`, symlink, size limits |
| `orphan_file_refs` | warning | Keys in secrets file not referenced by inspected documents |
| `redaction_self_test` | blocker | Process-local redaction removes probe material |

Secret **values** are never printed. Error messages from malformed secrets files
avoid echoing file content.

## secrets suggest-ref

```bash
rexecop secrets suggest-ref --env /operator/private/environment.yaml
rexecop secrets suggest-ref --env ./env.yaml --connector zabbix
```

Returns `rexecop.secrets_suggest_ref.v0.1` with existing refs and suggested
reference names for `http_api` and `ssh_readonly` connectors. It does not read
`REXECOP_SECRETS_FILE`, does not read `REXECOP_SECRET_*`, does not validate
resolution and does not print values.

### Safe operator posture

- Keep `REXECOP_SECRETS_FILE` mode `0600`, owned by the runtime user, outside git.
- Prefer `secret_ref` over inline values in every environment and catalog document.
- Use `secrets suggest-ref` for reference naming, then store values out of band.
- Run `secrets doctor` after editing environment YAML or the secrets file, before
  `plan` or staging connector tests.
