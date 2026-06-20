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

Never store resolved secrets under `.rexecop/` or commit them to git.

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
    allowlist:
      - action: uptime
        command: uptime
```

**Temporary operator tool:** until GovEngine policy engine owns remote command policy,
this backend provides read-only allowlisted SSH only. It refuses `apply` / `recovery` modes.
Do not treat it as a production authorization boundary on its own.

## Boundary

Proxmox, PBS, Zabbix, and similar platforms are **configuration targets** of `http_api`, not
hardcoded imports in `src/rexecop`. Profile connector YAML declares allowed capability names;
environment YAML declares how to reach APIs.
