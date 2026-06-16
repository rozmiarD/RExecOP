# Safety model

RExecOp is designed for controlled, auditable operations—not unconstrained automation.

## Hard rules (project-wide)

1. **No apply without governance** — mutating execution requires a positive GovEngine decision (Phase 2+).
2. **No ad hoc workflows** — only profile-declared steps may run.
3. **Evidence is mandatory** — important lifecycle transitions must leave a trace.
4. **Secrets never in store** — passwords, tokens, and API keys are redacted from evidence.
5. **LLM is not an executor** — models may analyze escalation packages later; they do not bypass RExecOp or GovEngine.
6. **Profiles stay out of core** — no Tecrax/Ravenclaw domain logic in `src/rexecop`.

## Phase 0 posture

- No real connectors
- No GovEngine or SCLite dependencies
- No infrastructure mutation
- CLI is non-interactive and informational only

## Future generic HTTP connector rule (Phase 9)

When `http_api` is introduced:

- It may invoke **only** capabilities declared in the profile connector contract for the target environment.
- Mutating calls require a prior GovEngine `allowed` decision for the operation/step.
- Undeclared capabilities are a boundary violation and must fail closed.

## Static GovEngine adapter (Phase 2A)

The static governance adapter is for bootstrap and tests only. It is **not** production governance and must be documented as such.

## Operator defaults (future)

- Default operation mode: `dry_run` or `observe`
- `apply` requires explicit mode selection and governance clearance
- Escalation packages list **descriptive** safe next options only—they are not auto-executed commands
