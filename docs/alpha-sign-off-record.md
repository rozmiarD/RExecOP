# Alpha sign-off record

> Operator-controlled record. Do not commit resolved secrets, tokens, or production credentials.

| Field | Value |
|-------|-------|
| RExecOp version (dev line) | `0.2.3a0` |
| RExecOp version (PyPI published) | `0.2.3a0` |
| Tecrax version | `0.3.2a0` |
| Operator | lab automation / operator host |
| Host / environment | dev workstation (`/home/probo/projects/rexecop`) |
| Date (UTC) | 2026-06-20 |

## Automated checks

| Check | Pass? | Notes |
|-------|-------|-------|
| `bash scripts/run_alpha_signoff_checks.sh` | [x] | delivery scope + public truth |
| `pytest -m delivery` | [x] | includes `test_stage_a_contracts.py` |
| GitHub Actions `main` green | [x] | commit: `a24d928` (ruff fix) |
| PyPI `rexecop==0.2.3a0` install smoke | [x] | `rexecop version` → `0.2.3a0` |
| PyPI `tecrax==0.3.2a0` install smoke | [x] | imports OK with rexecop pin |

## Human checklist (production-adjacent)

| # | Item | Pass? | Evidence |
|---|------|-------|----------|
| 1 | Runbooks and safety model read | [ ] | |
| 2 | Lab runbook completed on operator host | [ ] | cwd: _path_ |
| 3 | Dependency pins verified (`pip check`) | [ ] | |
| 4 | Read-only workflow succeeded on fixture/staging | [ ] | op: _id_ |
| 5 | No secrets in `.rexecop/` exports (`rg` clean) | [ ] | |
| 6 | Production config uses `GovEngineClient` | [ ] | |
| 7 | Alpha limitations accepted | [ ] | |

## Signature

Operator acceptance (name, date):

```
_______________________________________________
```

## Notes

- Etap A contract hardening landed in `0.2.3a0` (target validation, workflow contract, atomic FileStore, ssh_readonly docs).
- GovEngine policy engine for SSH remains a separate GovEngine roadmap item.
- This record contains **no** secret values by design.
