# Alpha sign-off

RExecOp `0.2.8a0` source line — formal operator acceptance before production-adjacent use.

This document separates **automated checks** (CI / script) from **human acceptance** (operator).

## Automated gate (run before signing)

From the RExecOp repository root:

```bash
bash scripts/run_alpha_signoff_checks.sh
```

The script runs:

1. `python scripts/validate_public_truth.py`
2. Core boundary greps (`tecrax` / domain strings forbidden in core)
3. `scripts/secret_scan.sh`
4. `pytest -m delivery` — canonical delivery-scope suite from `tests/delivery_scope.py`
5. Optional `python -m build` + `twine check` when `REXECOP_SIGNOFF_BUILD=1` and `build` is installed

CI on `main` runs the full pytest suite plus the `package-dry-run` job. PyPI publication
uses `.github/workflows/publish.yml` (manual) or operator `twine upload` after sign-off.

## Human acceptance checklist

Record completion in [alpha-sign-off-record.md](alpha-sign-off-record.md).

| # | Item | Evidence |
|---|------|----------|
| 1 | Read [OPERATOR_RUNBOOK.md](../OPERATOR_RUNBOOK.md) and [safety-model.md](safety-model.md) | initials / date |
| 2 | Complete [OPERATOR_LAB_RUNBOOK.md](../OPERATOR_LAB_RUNBOOK.md) checklist | lab directory path |
| 3 | GovEngine + SCLite pins match `pyproject.toml` | `pip show govengine sclite-core` |
| 4 | Read-only path on fixture or staging `http_api` succeeded | operation id |
| 5 | `.rexecop/` exports contain no plaintext secrets | `rg` clean |
| 6 | `GovEngineClient` used on operator host (not `StaticGovEngineAdapter`) | config review |
| 7 | Alpha limitations accepted for intended use | signature |

## What sign-off does **not** mean

- Not a security audit or compliance certification
- Not approval for unmanned apply on critical infrastructure
- Not a promise of production-ready governance (alpha limits remain)

## Related

- [known-limitations.md](known-limitations.md)
- [distribution.md](distribution.md)
