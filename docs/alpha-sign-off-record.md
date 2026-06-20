# Alpha sign-off record

> Copy this file outside git or fill in place only on operator-controlled storage.
> Do not commit resolved secrets or production tokens.

| Field | Value |
|-------|-------|
| RExecOp version | `0.2.2a0` |
| Tecrax version | `0.3.2a0` |
| Operator | _name / team_ |
| Host / environment | _hostname or lab id_ |
| Date (UTC) | _YYYY-MM-DD_ |

## Automated checks

| Check | Pass? | Notes |
|-------|-------|-------|
| `bash scripts/run_alpha_signoff_checks.sh` | [x] | delivery scope + public truth (dev host) |
| GitHub Actions `main` green | [ ] | commit: _sha_ |
| PyPI `rexecop==0.2.2a0` install smoke | [ ] | `pip install rexecop==0.2.2a0 && rexecop version` |

## Human checklist

| # | Item | Pass? | Evidence |
|---|------|-------|----------|
| 1 | Runbooks and safety model read | [ ] | |
| 2 | Lab runbook completed | [ ] | cwd: _path_ |
| 3 | Dependency pins verified | [ ] | |
| 4 | Read-only workflow succeeded | [ ] | op: _id_ |
| 5 | No secrets in `.rexecop/` exports | [ ] | |
| 6 | Production uses `GovEngineClient` | [ ] | |
| 7 | Alpha limitations accepted | [ ] | |

## Signature

Operator acceptance (name, date):

```
_______________________________________________
```

## Publication note

Public PyPI (`15.1c`) for `rexecop==0.2.2a0` proceeds after automated delivery gate passes.
Human items 1–7 remain required for production-adjacent use.
