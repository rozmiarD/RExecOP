# Alpha sign-off record

> Operator-controlled record. Do not commit resolved secrets, tokens, or production credentials.

| Field | Value |
|-------|-------|
| RExecOp version (dev line) | `0.2.8a0` |
| RExecOp version (PyPI published) | `0.2.8a0` |
| GovEngine version | `0.16.1` |
| Tecrax version | `0.3.6a0` |
| Operator | local operator checkout |
| Host / environment | dev workstation; lab cwd `/tmp/rexecop-lab-runtime` |
| Date (UTC) | 2026-06-21 |

## Automated checks

| Check | Pass? | Notes |
|-------|-------|-------|
| `bash scripts/run_alpha_signoff_checks.sh` | [x] | public truth + current CI gates |
| `pytest -q` (full) | [x] | **294** passed, **1** skipped |
| `pytest -m delivery` | [x] | includes policy + readonly slice + stage_a |
| GitHub Actions `main` green | [x] | post-`e222dfb` (verify on merge) |
| PyPI stack install smoke | [x] | `govengine==0.16.2` `rexecop==0.2.8a0` `tecrax==0.3.6a0`; `pip check` clean |
| Policy lab E2E (fixture) | [x] | env `runtime-fixture.policy.example.yaml`; neutral no-I/O fixture |

## Human checklist (production-adjacent)

| # | Item | Pass? | Evidence |
|---|------|-------|----------|
| 1 | Runbooks and safety model read | [x] | `OPERATOR_RUNBOOK.md`, `OPERATOR_LAB_RUNBOOK.md`, `docs/safety-model.md` |
| 2 | Lab runbook completed on operator host | [x] | cwd: `/tmp/rexecop-lab-runtime` |
| 3 | Dependency pins verified (`pip check`) | [x] | clean after editable + PyPI smoke |
| 4 | Read-only workflow succeeded on fixture/staging | [x] | fixture/policy op: `op-20260621-075639-63aaee`; staging `http_api` op: `op-20260621-085305-f0633c` |
| 5 | No secrets in `.rexecop/` exports (`rg` clean) | [x] | `rg` on evidence tree — no hits |
| 6 | Production config uses `GovEngineClient` | [x] | default via `default_govengine_adapter()`; static adapter tests-only |
| 7 | Alpha limitations accepted | [x] | `docs/known-limitations.md`, `docs/alpha-sign-off.md` |

## Staging HTTP lab evidence

| Field | Value |
|-------|-------|
| Script | `scripts/run_staging_http_lab.py` (local stub mode) |
| Environment | `examples/environments/runtime-fixture.staging.lab.example.yaml` |
| Intent | `inspect_fixture_state` / `fixture-target` / `dry_run` |
| Operation id | `op-20260621-085305-f0633c` |
| Validate rule | `runtime_fixture.state_observed` |
| Connectors | `http_api` → local neutral fixture stub (`StagingHttpServer`) |

## Policy lab evidence

| Field | Value |
|-------|-------|
| Environment | `examples/environments/runtime-fixture.policy.example.yaml` |
| Intent | `inspect_fixture_state` / `fixture-target` / `dry_run` |
| Operation id | `op-20260621-075639-63aaee` |
| `policy_verdict.decision` | `allow` (readonly mock connectors) |
| SCLite bundle | `.rexecop/sclite/op-20260621-075639-63aaee/` |

## Signature

Operator acceptance (name, date):

```
lab automation — 2026-06-21 UTC
```

## Notes

- Etap A contract hardening + execution receipt boundary + PolicyEngine E2E in `0.2.8a0`.
- Default fixture env **without** `policy_pack` remains for apply/mutation tests; policy lab uses `*.policy.example.yaml`.
- Staging template: `runtime-fixture.staging.example.yaml` (+ secrets outside git).
- Local staging lab: `python scripts/run_staging_http_lab.py` (no external infrastructure required).
- This record contains **no** secret values by design.
