# Connector contract

RExecOp dispatches connector steps through a **ConnectorRuntime** port. Domain
semantics stay in profiles and environment configuration — not in rexecop core.

## Backends (Phase 9)

| Backend | Purpose |
| --- | --- |
| `mock` | Bootstrap / offline tests |
| `http_api` | Config-driven JSON REST for Proxmox, PBS, etc. |
| `local_shell_readonly` | Allowlisted non-mutating host commands |

Environment YAML selects the backend per connector:

```yaml
connectors:
  proxmox:
    enabled: true
    backend: http_api
    base_url_secret_ref: proxmox_base_url
    auth:
      secret_ref: proxmox_api_token
      header: Authorization
      prefix: "PVEAPIToken="
    actions:
      list_vms:
        method: GET
        path: /api2/json/cluster/resources
        unwrap: data
```

## Safety rules

1. **Profile capabilities** — `http_api` may invoke only actions declared in the
   profile connector contract (`profile/connectors/<name>.yaml`).
2. **Governance** — mutating actions require GovEngine `allowed` and apply mode.
3. **Secrets** — use `secret_ref` / `base_url_secret_ref`; inline secrets in
   environment YAML are rejected.
4. **Evidence** — connector responses are redacted before persistence.

## Error taxonomy

| `error_class` | Meaning |
| --- | --- |
| `timeout` | Request timed out |
| `transient_connector_error` | Retryable HTTP/network failure |
| `policy_denied` | Read-only mode or governance block |
| `capability_undeclared` | Action not in profile contract |
| `auth_failed` | HTTP 401/403 |
| `connector_disabled` | Connector disabled in environment |

## Secrets port

Resolve via:

- `REXECOP_SECRET_<REF>` environment variables
- `REXECOP_SECRETS_FILE` YAML (`secrets.<ref>`)

Never store resolved secrets under `.rexecop/`.
