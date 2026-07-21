# Alpha sign-off

RExecOp `0.3.0rc3` source candidate — formal operator acceptance before
production-adjacent use. This candidate is not published; `0.2.24a0` remains
the latest PyPI alpha line.

This document separates **automated checks** (CI / script) from **human acceptance** (operator).

## Automated gate (run before signing)

From the RExecOp repository root:

```bash
bash scripts/run_alpha_signoff_checks.sh
```

The script runs:

1. `python scripts/validate_public_truth.py`
2. `python scripts/validate_stack_contracts.py`
3. `python scripts/validate_profile_conformance.py`
4. `python scripts/validate_first_run_smoke.py`
5. `python scripts/validate_operator_journeys.py`
6. `python scripts/validate_cross_repo_golden_fixture.py`
7. `python scripts/validate_workflow_security.py`
8. `python scripts/validate_stack_invariants.py`
9. `python scripts/validate_external_review_gate.py`
10. `python scripts/validate_m86_security_gate.py`
11. `python scripts/validate_m9_runtime_gate.py`
12. `python scripts/validate_m95n_gate.py`
13. `python scripts/validate_m10_readonly_gate.py` — default/readiness/connector/Tecrax
    mutation block
14. `python scripts/validate_m10_runtime_gate.py` — M9 dependency, stable storage,
    single executor, mutation posture, plugin inventory and runtime security blockers
15. `python scripts/validate_m10_public_api_gate.py` — fresh Python imports, full
    CLI stability classification, schema fail-closed and alpha-to-1.0 new-root policy
16. `python scripts/validate_m10_release_gate.py` — pinned actions, OIDC workflow,
    SBOM/attestation-bound release evidence and fail-closed release regressions;
    add `--live-github` before publication to verify ref/environment protection
17. `python scripts/validate_g3_runtime_governance_gate.py`
18. `python scripts/validate_governance_conformance.py`
19. `python scripts/validate_g6_release_candidate_gate.py`
20. Core boundary greps (`tecrax` / domain strings forbidden in core) and
    `scripts/secret_scan.sh`
21. Ruff and mypy
22. `pytest -m delivery` — canonical delivery-scope suite from `tests/delivery_scope.py`
23. Optional `python -m build` + `twine check` + `validate_artifact_install_smoke.py`
   when `REXECOP_SIGNOFF_BUILD=1` and `build` is installed

The release workflow additionally runs
`python scripts/validate_release_train_preflight.py --release --previous-evidence
<downloaded-json>` before upload. Post-publish it runs
`python scripts/validate_public_index_release_smoke.py --write-evidence --verify-post-publish`
    (wraps `validate_clean_install_smoke.py`, `rexecop version`, `rexecop --json doctor`, creates
    SBOM/attestation-bound `rexecop.release_evidence.v2`, then verifies it before
    durable evidence persistence).
Package supply-chain validation is `python scripts/validate_supply_chain_gate.py dist`
after build (`pip-audit` + CycloneDX SBOM; exceptions in
`docs/supply-chain-audit-exceptions.json`).

CI on `main` runs the same validators (except the optional build step), **ruff**, **mypy**,
and the full **pytest** suite on Python **3.11**, **3.12**, and **3.13**, plus the
`package-dry-run` job. PyPI publication uses `.github/workflows/publish.yml`
(manual), the named `pypi` environment and PyPI Trusted Publishing OIDC after
sign-off. Local/operator token uploads are not an accepted release path.

## Human acceptance checklist

Record completion in [alpha-sign-off-record.md](alpha-sign-off-record.md).

| # | Item | Evidence |
|---|------|----------|
| 1 | Read [OPERATOR_RUNBOOK.md](../OPERATOR_RUNBOOK.md) and [safety-model.md](safety-model.md) | initials / date |
| 2 | Complete [OPERATOR_LAB_RUNBOOK.md](../OPERATOR_LAB_RUNBOOK.md) checklist | runtime root path |
| 3 | GovEngine + SCLite pins match `pyproject.toml` | `pip show govengine sclite-core` |
| 4 | Read-only path on fixture or staging `http_api` succeeded | operation id |
| 5 | Runtime root exports contain no plaintext secrets | `rg` clean |
| 6 | `GovEngineClient` used on operator host (not `StaticGovEngineAdapter`) | config review |
| 7 | Alpha limitations accepted for intended use | signature |

## What sign-off does **not** mean

- Not a security audit or compliance certification
- Not approval for unmanned apply on critical infrastructure
- Not a promise of production-ready governance (alpha limits remain)

## Related

- [known-limitations.md](known-limitations.md)
- [distribution.md](distribution.md)
- [first-run.md](first-run.md)
