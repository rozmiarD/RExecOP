# Connector contract

RExecOp dispatches connector steps through a **ConnectorRuntime** port. Domain semantics stay
in profiles and environment configuration — not in rexecop core.

## Runtime routing

`CompositeConnectorRuntime` selects a backend per connector entry in the environment YAML
(stored on the operation as sanitized `environment_connectors`).

| Backend | Purpose |
| --- | --- |
| `mock` | Bootstrap, offline tests, default when `backend` omitted |
| `http_api` | Config-driven JSON REST (Proxmox, PBS, etc. as config instances) |
| `local_shell_readonly` | Allowlisted non-mutating host commands |
| `ssh_readonly` | Temporary read-only remote SSH allowlist (documented non-production policy path) |
| _plugin EP_ | Registered `rexecop.connector_backends` name (e.g. `tecrax_proxmox`, `tecrax_fixture`) |

Factory: `rexecop.connectors.composite_runtime.build_connector_runtime()`.

## Environment configuration

```yaml
connectors:
  proxmox:
    enabled: true
    backend: http_api
    base_url_secret_ref: proxmox_base_url   # or base_url for staging/lab
    auth:
      secret_ref: proxmox_api_token
      header: Authorization
      prefix: "PVEAPIToken="
    timeout_seconds: 10
    max_response_bytes: 65536
    retry:
      max_attempts: 3
      base_delay: 0.2
      max_delay: 2.0
      on: [timeout, transient_connector_error]
    actions:
      list_vms:
        method: GET
        path: /api2/json/cluster/resources
        unwrap: data
        pagination:
          items_path: data
          next_path: next
          max_pages: 10
      restart:
        method: POST
        path: /api2/json/nodes/{node}/qemu/{vmid}/status/restart
        mutating: true
```

Templates:

- `examples/environments/small-public-unit-proxmox.example.yaml` — mock connectors
- `examples/environments/small-public-unit-proxmox.staging.example.yaml` — `http_api` + `secret_ref`
- `examples/environments/small-public-unit-proxmox.staging.lab.example.yaml` — local lab stub (`base_url`)
- `examples/secrets/staging-http.lab.example.yaml` — secrets template for real staging

## Safety rules

1. **Profile capabilities** — `http_api` may invoke only actions listed in
   `profile/connectors/<name>.yaml` `capabilities`.
2. **Governance** — mutating actions require GovEngine `allowed`, apply mode, and runtime
   `mutating_allowed` on the connector runtime.
3. **Read-only modes** — `dry_run`, `observe`, `emergency_readonly` refuse mutating actions.
4. **Secrets** — use `secret_ref` / `base_url_secret_ref`; inline secrets in environment YAML
   are rejected at plan time.
5. **Evidence** — connector responses pass through `redact_payload()` before persistence.
6. **HTTP failures** — `http_api` sets `error_class`, `status_code`, and a redacted `body_snippet`
   when the upstream API returns an HTTP error body.
7. **HTTP response bounds** — successful bodies are read only up to
   `max_response_bytes + 1` (default `65536`); oversized responses fail before JSON parsing
   and are not persisted.

## Error taxonomy

| `error_class` | Meaning |
| --- | --- |
| `timeout` | Request or shell command timed out |
| `transient_connector_error` | Retryable HTTP/network failure |
| `policy_denied` | Read-only mode or governance block |
| `capability_undeclared` | Action not in profile connector contract |
| `auth_failed` | HTTP 401/403 |
| `validation_failed` | Config or response shape error |
| `connector_disabled` | Connector `enabled: false` in environment |
| `unsupported` | Unknown connector or backend mismatch |

## Secrets port

Resolve via `rexecop.secrets` (`ChainedSecretResolver`):

| Source | Mechanism |
| --- | --- |
| Environment variables | `REXECOP_SECRET_<REF>` (ref normalized to uppercase underscores) |
| Secrets file | `REXECOP_SECRETS_FILE` pointing at YAML with `secrets.<ref>` |

Never store resolved secrets under `.rexecop/` or commit them to git. The secrets file must
be a regular file owned by the current user, with mode `0600` or stricter; symlinks and
group/world-readable files are rejected.

## local_shell_readonly

```yaml
connectors:
  host_probe:
    enabled: true
    backend: local_shell_readonly
    allowlist:
      - action: uptime
        command: uptime
```

Refuses mutating operation modes. Only allowlisted `action` / `command` pairs may run.
Allowlist entries are validated with `govengine.execution.command_shape.normalize_argv`.
RExecOp additionally rejects structured mutation patterns before subprocess execution:
shell `-c`, `sudo`, service/systemd lifecycle mutations, Docker mutations, and Docker
Compose `up`/`down`/`restart`. Matching uses argv tokens and command families, not
substring scanning.

Optional `max_output_bytes` (default `65536`) bounds stored stdout/stderr. Responses include
`output_digests` (SHA-256 of full capture), `output_truncated`, and `output_sizes`.

## ssh_readonly (temporary)

```yaml
connectors:
  pve_ro:
    enabled: true
    backend: ssh_readonly
    host: pve-01.example.com
    user: readonly
    port: 22
    identity_file_secret_ref: pve_ssh_key
    known_hosts_policy: accept-new   # accept-new | strict | no
    known_hosts_file: /path/to/known_hosts   # optional with strict
    allowlist:
      - action: uptime
        command: uptime
```

**Policy layers:** when `environment.policy_pack` is set, GovEngine `PolicyEngine` runs at plan (operation) and at each connector invoke before backends execute. Allowlists and read-only mode checks remain as a second layer for shell/SSH backends.

Connector execution currently accepts only a plain `allow` verdict with no obligations
or constraints. `allow_with_obligations`, or any verdict carrying controls RExecOp cannot
enforce, is blocked before the backend with `unsupported_policy_controls`. RExecOp does
not claim that receipt, output-limit, timeout, or other obligations are fulfilled merely
because GovEngine returned them.

### Risk notes

| Topic | Behavior |
| --- | --- |
| `known_hosts_policy` | Default `accept-new` pins host keys on first connect. Use `strict` with a managed `known_hosts_file` in production-adjacent labs. `no` disables host key checking — **lab-only**. |
| Remote command quoting | Allowlisted argv is joined with `shlex.quote` before passing as the remote SSH command. |
| Remote shell | OpenSSH still invokes the remote user shell to run the command string — keep allowlists minimal. |
| Secrets | `identity_file_secret_ref` resolves via `REXECOP_SECRETS_FILE`; never commit key material. |
| Policy ownership | `environment.policy_pack` → `PolicyEngine` at invoke; allowlist + mode checks remain for shell backends |

## Boundary

Proxmox, PBS, Zabbix, and similar platforms are **configuration targets** of `http_api`, not
hardcoded imports in `src/rexecop`. Profile connector YAML declares allowed capability names;
environment YAML declares how to reach APIs.
