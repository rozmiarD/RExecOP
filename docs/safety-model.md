# Safety model

RExecOp is designed for controlled, auditable operations—not unconstrained automation.

## Hard rules (project-wide)

1. **No apply without governance** — mutating execution requires a positive GovEngine decision (Phase 2+).
2. **No ad hoc workflows** — only profile-declared steps may run.
3. **Evidence is mandatory** — important lifecycle transitions must leave a trace.
4. **Secrets never in store** — passwords, tokens, and API keys are redacted from evidence; environment YAML must use `secret_ref`.
5. **LLM is not an executor** — models may analyze escalation packages later; they do not bypass RExecOp or GovEngine.
6. **Profiles stay out of core** — no Tecrax/Ravenclaw domain logic in `src/rexecop`.

## Phase 9 connector posture

- `http_api` is generic — Proxmox/PBS are **environment config instances**, not hardcoded core logic.
- `http_api` may invoke **only** capabilities declared in the profile connector contract.
- Mutating `http_api` calls require GovEngine `allowed` for the operation.
- `local_shell_readonly` refuses apply/recovery modes; commands must be allowlisted.
- Connector responses pass through evidence redaction (including API-shaped payloads).

## Static GovEngine adapter (Phase 2A)

The static governance adapter is for bootstrap and tests only. It is **not** production governance and must be documented as such.

## Operator defaults

- Default operation mode: `dry_run` or `observe`
- `apply` requires explicit mode selection and governance clearance
- Escalation packages list **descriptive** safe next options only—they are not auto-executed commands
- Real environment files live **outside git**; use `small-public-unit-proxmox.staging.example.yaml` as a template
