# Connector contract

RExecOp dispatches connector steps through a **ConnectorRuntime** port. Domain semantics stay
in profiles and environment configuration — not in rexecop core.

## Runtime routing

`CompositeConnectorRuntime` selects a backend per connector entry in the environment YAML
(stored on the operation as sanitized `environment_connectors`).

| Backend | Purpose |
| --- | --- |
| `mock` | Bootstrap, offline tests, default when `backend` omitted |
| `http_api` | Config-driven JSON REST for profile-declared API actions |
| `local_shell_readonly` | Allowlisted non-mutating host commands |
| `ssh_readonly` | Temporary read-only remote SSH allowlist (documented non-production policy path) |
| _plugin EP_ | Registered `rexecop.connector_backends` name owned by an external package |

Factory: `rexecop.connectors.composite_runtime.build_connector_runtime()`.

## Environment configuration

```yaml
connectors:
  fixture_source:
    enabled: true
    backend: http_api
    deployment_posture: stable
    base_url_secret_ref: fixture_base_url   # or base_url for local lab stubs
    destination_binding:
      scheme: https
      effective_port: 443
      address_class: dns_name
      origin_binding_digest: "sha256:<normalized-origin-digest>"
    operator_egress_enforced: true
    dns_rebinding_protection: operator_egress
    tls:
      ca_file_secret_ref: fixture_ca_file   # optional operator-managed CA path
    auth:
      secret_ref: fixture_api_token
      header: Authorization
      prefix: "Bearer "
    timeout_seconds: 10
    max_response_bytes: 65536
    retry:
      max_attempts: 3
      base_delay: 0.2
      max_delay: 2.0
      on: [timeout, transient_connector_error]
    actions:
      read_fixture_state:
        method: GET
        path: /fixture/state
        unwrap: state
      apply_fixture_change:
        method: POST
        path: /fixture/change
        mutating: true
```

Templates:

- `examples/environments/runtime-fixture.example.yaml` — mock connectors
- `examples/environments/runtime-fixture.staging.example.yaml` — `http_api` + `secret_ref`
- `examples/environments/runtime-fixture.staging.lab.example.yaml` — local lab stub (`base_url`)
- `examples/secrets/staging-http.lab.example.yaml` — secrets template for real staging

## Safety rules

1. **Profile capabilities** — `http_api` may invoke only actions listed in
   `profile/connectors/<name>.yaml` `capabilities`.
2. **Governance** — mutating actions require GovEngine `allowed`, apply mode, and runtime
   `mutating_allowed` on the connector runtime.
3. **Read-only modes** — `dry_run`, `observe`, `emergency_readonly` refuse mutating actions.
4. **Secrets** — use `secret_ref` / `base_url_secret_ref`; inline secrets in environment YAML
   are rejected at plan time.
5. **Evidence** — connector responses pass through exact-path profile-declared
   `public_projection.safe_fields` allowlists, then `redact_payload()` before
   persistence. Wildcard subtrees are rejected; bodies and structured
   before/after state are digest-only unless an exact path is declared.
6. **HTTP failures** — `http_api` sets `error_class`, `status_code`, and a redacted `body_snippet`
   when the upstream API returns an HTTP error body.
7. **HTTP response bounds** — successful bodies are read only up to
   `max_response_bytes + 1` (default `65536`); oversized responses fail before JSON parsing
   and are not persisted.
8. **TLS** — HTTPS verifies certificates and hostnames. A private/self-signed CA may be
   selected only through `tls.ca_file_secret_ref`; insecure verification flags are rejected.
   The referenced CA file and any host-specific trust material stay outside git.
9. **Destination posture** — `deployment_posture: stable` is the live default and
   requires HTTPS. DNS destinations additionally require
   `operator_egress_enforced: true` plus
   `dns_rebinding_protection: operator_egress`. Private/loopback/link-local stable
   destinations require `network_scope: policy_bound`. Plain HTTP is available only
   under explicit `lab` or `fixture` posture.
10. **Secret endpoints** — when `base_url_secret_ref` hides the endpoint from plan
    compilation, the environment must declare the bounded `destination_binding`
    (`scheme`, `effective_port`, `address_class`, `origin_binding_digest`). Runtime
    resolution must match it before connector IO.

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
    deployment_posture: stable       # stable | lab | fixture
    known_hosts_policy: strict       # stable requires strict
    known_hosts_file: /path/to/known_hosts
    allowlist:
      - action: uptime
        command: uptime
```

**Policy layers:** when `environment.policy_pack` is set, GovEngine `PolicyEngine` runs at plan (operation) and at each connector invoke before backends execute. Allowlists and read-only mode checks remain as a second layer for shell/SSH backends.

Operation planning accepts plain `allow` and GovEngine `allow_with_obligations` only for
the supported neutral B2 controls documented in [execution-contract.md](execution-contract.md).
RExecOp validates the digest-bound admission before IO and enforces those controls.
Connector-level evaluation remains plain-allow-only; connector-specific obligations,
`approval_required`, `deny`, unknown controls, and unsupported timeout backends are blocked.

### Risk notes

| Topic | Behavior |
| --- | --- |
| `known_hosts_policy` | Default `strict` requires an operator-managed `known_hosts_file`. `accept-new` is accepted only with explicit `lab`/`fixture` posture. `no` is unavailable in stable posture. |
| Operator files | Identity and known-hosts paths must exist, be regular non-symlink files, have the operator owner, and pass permission checks before connector IO. |
| Remote command quoting | Allowlisted argv is joined with `shlex.quote` before passing as the remote SSH command. |
| Remote shell | OpenSSH still invokes the remote user shell to run the command string — keep allowlists minimal. |
| Secrets | `identity_file_secret_ref` resolves via `REXECOP_SECRETS_FILE`; never commit key material. |
| Policy ownership | `environment.policy_pack` → `PolicyEngine` at invoke; allowlist + mode checks remain for shell backends |

## Discoverability CLI

Neutral connector backend metadata is exposed without backend IO:

```bash
rexecop connectors list
rexecop connectors show http_api
rexecop capabilities list
```

Built-in backends report `certification_tier: core` (or `bootstrap` for `mock`),
plus M6 security posture fields: `identity_class`, `egress_class`,
`read_only_backend` and `live_backend_capable`. Per-connector env bindings
compile digest-bound `rexecop.backend_capability_descriptor.v0.1` projections
during typed execution (secret-ref requirements and redacted network boundary,
without resolved secrets or hosts). Raw shell backends and undeclared backend
classes fail closed before backend IO.

Plugin entry points report `certification_tier: plugin`. See
[profile-developer-surface.md](profile-developer-surface.md).
They execute as trusted in-process code under the versioned factory contract; RExecOp does
not claim process isolation or sandboxing. Compatibility reports bound plugin exceptions and
never include raw plugin exception text.

## Boundary

Infrastructure products are **profile/operator configuration targets** of generic connectors,
not hardcoded imports in `src/rexecop`. Profile connector YAML declares allowed capability
names and immutable action shapes; environment YAML declares how to reach APIs.
