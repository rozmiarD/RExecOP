# Safety model

RExecOp implements **Regulated Execution Operations** — controlled, auditable workflow execution,
not unconstrained automation.

## Hard rules

1. **Stable means read-only** — the default `stable_read_only` runtime posture blocks
   `apply` and `recovery` before execution and again before connector I/O.
2. **No apply without governance** — the explicit `lab_only` mechanics posture does not
   bypass GovEngine admission/approval/authorization or RExecOp runtime-permit,
   lease and fencing checks.
3. **No ad hoc workflows** — only profile-declared steps may run; the workflow runner never
   invents steps.
4. **Evidence is mandatory** — state transitions and step boundaries emit internal evidence events.
5. **Secret values are prohibited from evidence and configuration** — environment
   YAML must use `secret_ref`; supported redaction and validation are applied at
   persistence boundaries. The runtime root remains sensitive and requires
   operator review before sharing.
6. **LLM is not an executor** — models may analyze escalation packages later; they do not bypass
   RExecOp or GovEngine.
7. **Profiles stay out of core** — no Tecrax/Ravenclaw domain logic in `src/rexecop` (CI grep).

## Connector posture

- `http_api` is generic — infrastructure APIs are environment config instances, not core code.
- `http_api` may invoke **only** capabilities declared in the profile connector contract.
- Mutating `http_api` calls require GovEngine `allowed` for the operation and apply mode.
- `local_shell_readonly` refuses `apply` / `recovery` modes; commands must be allowlisted.
- Connector responses pass through evidence redaction (including API-shaped payloads).

## GovEngine adapter posture

| Adapter | Runtime role |
| --- | --- |
| `GovEngineClient` | Default in-process adapter; trust still depends on host authority/verifier configuration |
| `StaticGovEngineAdapter` | Bootstrap and tests only; not a governance boundary |

Selecting `GovEngineClient` alone is not production certification. The static
adapter is rejected as a real governance boundary in code, tests and
[GovEngine integration](govengine-integration.md).

## Operator defaults

- Default operation mode: `dry_run` (CLI default on `plan`)
- `apply` requires explicit mode selection, `REXECOP_MUTATION_POSTURE=lab_only`, GovEngine
  clearance, and approval when required; `lab_only` is not stable or production certification
- Escalation packages list **descriptive** safe next options — they are not auto-executed commands
- Real environment and secrets files live **outside git**; use `*.example.yaml` templates in-repo
- Target lock and queue limit concurrent mutating work per environment policy
- Maintenance windows block apply when configured

## Runtime storage

Runtime roots (`--root`, named `--instance`, or fallback `./.rexecop`) are gitignored.
Operators must verify exports and evidence do not contain resolved secrets before sharing
artifacts outside the host.

## Release-candidate limits

RExecOp `1.0.0rc1` is a release candidate with a stable read-only core. Alpha
classifications still apply to explicitly listed CLI/API surfaces, SQLite and
legacy runtime-root compatibility; they do not describe the entire package.
See [known-limitations.md](known-limitations.md) and
[OPERATOR_RUNBOOK.md](../OPERATOR_RUNBOOK.md) for explicit non-claims.

## Related documents

- [architecture.md](architecture.md) — layer boundaries
- [connector-contract.md](connector-contract.md) — `http_api` and secrets
- [govengine-integration.md](govengine-integration.md) — apply gating
- [sclite-integration.md](sclite-integration.md) — evidence contracts and verification
