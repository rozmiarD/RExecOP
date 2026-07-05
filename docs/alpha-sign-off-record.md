# Alpha sign-off record

> Operator-controlled record. Do not commit resolved secrets, tokens, or production credentials.

| Field | Value |
|-------|-------|
| RExecOp version (dev line) | `0.2.18a0` |
| RExecOp version (PyPI published) | `0.2.18a0` |
| GovEngine version | `0.16.5` |
| SCLite version | `1.0.8` |
| Tecrax version | `0.3.9a0` |
| Operator | |
| Host / environment | |
| Runtime root used for lab | |
| Date (UTC) | |

## Automated checks

| Check | Pass? | Notes |
|-------|-------|-------|
| `bash scripts/run_alpha_signoff_checks.sh` | [ ] | public truth, stack contracts, profile conformance, first-run smoke |
| `pytest -q` (full) | [ ] | |
| `pytest -m delivery` | [ ] | canonical delivery-scope suite |
| GitHub Actions `main` green | [ ] | |
| PyPI stack install smoke | [ ] | `pip check` clean on published pins |
| First-run smoke | [ ] | `python scripts/validate_first_run_smoke.py` |

## Human checklist (production-adjacent)

| # | Item | Pass? | Evidence |
|---|------|-------|----------|
| 1 | Runbooks and safety model read | [ ] | `OPERATOR_RUNBOOK.md`, `OPERATOR_LAB_RUNBOOK.md`, `docs/safety-model.md` |
| 2 | Lab runbook completed on operator host | [ ] | runtime root path |
| 3 | Dependency pins verified (`pip check`) | [ ] | |
| 4 | Read-only workflow succeeded on fixture/staging | [ ] | operation id |
| 5 | No secrets in runtime root exports (`rg` clean) | [ ] | |
| 6 | Production config uses `GovEngineClient` | [ ] | not `StaticGovEngineAdapter` |
| 7 | Alpha limitations accepted | [ ] | `docs/known-limitations.md`, `docs/alpha-sign-off.md` |

## First-run evidence (optional)

| Field | Value |
|-------|-------|
| Runtime root | |
| Profile | `examples/first-run-demo/profile/profile.yaml` |
| Operation id | |
| `doctor` status | |

## Staging HTTP lab evidence (optional)

| Field | Value |
|-------|-------|
| Script | `scripts/run_staging_http_lab.py` |
| Environment | |
| Intent | |
| Operation id | |
| Validate rule | |

## Signature

Operator acceptance (name, date):

```

```

## Notes

- Prior sign-off for `0.2.9a0` is archived at [archive/alpha-sign-off-record-2026-06-21-0.2.9a0.md](archive/alpha-sign-off-record-2026-06-21-0.2.9a0.md).
- This record contains **no** secret values by design.