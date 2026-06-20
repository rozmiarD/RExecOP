# Alpha sign-off record

> Copy this file outside git or fill in place only on operator-controlled storage.
> Do not commit resolved secrets or production tokens.

| Field | Value |
|-------|-------|
| RExecOp version | `0.2.1a0` |
| Tecrax version | `0.3.2a0` |
| Operator | _name / team_ |
| Host / environment | _hostname or lab id_ |
| Date (UTC) | _YYYY-MM-DD_ |

## Automated checks

| Check | Pass? | Notes |
|-------|-------|-------|
| `bash scripts/run_alpha_signoff_checks.sh` | [ ] | |
| GitHub Actions `main` green | [ ] | commit: _sha_ |

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

## Next step after sign-off

Public PyPI publication (`15.1c`) may proceed only after this record is completed and
operator explicitly approves index publication.
