# Release qualification

This procedure combines automated release gates with explicit human acceptance
for a RExecOp release candidate. Qualification does not widen the stable
read-only posture or override [Known limitations](known-limitations.md).

The compatibility name of the aggregate script remains:

```bash
bash scripts/run_alpha_signoff_checks.sh
```

The script name is historical. It is not the maturity label of the complete
`1.0.0rc1` package.

## Automated gate

The aggregate script runs the current release qualification surfaces:

- public truth, stack contracts and profile conformance;
- first-run, operator-journey and cross-repository fixture checks;
- workflow security, stack invariants and external-review validation;
- runtime, read-only, public-API and release gates;
- operational qualification and governance conformance;
- core-boundary and secret scans;
- Ruff, mypy and `pytest -m delivery`;
- optional build, `twine check` and
  `validate_artifact_install_smoke.py` when
  `REXECOP_SIGNOFF_BUILD=1`.

Important individual commands include:

```bash
python scripts/validate_public_truth.py
python scripts/validate_stack_contracts.py
python scripts/validate_profile_conformance.py
python scripts/validate_first_run_smoke.py
python scripts/validate_operator_journeys.py
python scripts/validate_cross_repo_golden_fixture.py
python scripts/validate_external_review_gate.py --version 1.0.0rc1
python scripts/validate_m10_public_api_gate.py
python scripts/validate_m10_release_gate.py
python scripts/validate_m10_operational_gate.py
python scripts/validate_artifact_install_smoke.py
python scripts/validate_clean_install_smoke.py
```

Before publication, run the release gate with live GitHub validation:

```bash
python scripts/validate_m10_release_gate.py --live-github
```

The publish workflow additionally:

- resolves and checks out the immutable `v<version>` tag;
- verifies dependency refs and release-train preconditions;
- builds wheel, sdist and CycloneDX SBOM;
- validates supply-chain and artifact-install surfaces;
- publishes through the protected `pypi` environment and Trusted Publishing;
- creates GitHub provenance attestations and release-evidence assets;
- performs a clean public-index install smoke after publication.

CI does not literally invoke every command from the aggregate script. It runs
the workflow-defined public-truth, compatibility, profile, first-run,
operator-journey, workflow-security, package, lint, typing and test surfaces.
The aggregate qualification gate is the broader local/release check. Consult
`.github/workflows/ci.yml` and `scripts/run_alpha_signoff_checks.sh` for the
executable source of truth.

## Human acceptance

Record completion in
[release-qualification-record.md](release-qualification-record.md).

| # | Item | Required evidence |
| --- | --- | --- |
| 1 | Operator and lab runbooks reviewed | Initials and date |
| 2 | Lab checklist completed on an isolated runtime root | Root identifier, not private contents |
| 3 | Exact GovEngine and SCLite pins verified | `pip check` / installed versions |
| 4 | Bounded read-only fixture or staging path succeeded | Operation identifier |
| 5 | Public projections reviewed for disclosure | Redacted review result |
| 6 | Static/test governance adapter absent from the intended host path | Configuration review |
| 7 | Independent release security review gate passed | Versioned review record |
| 8 | Release-candidate claims and non-claims accepted | Signature |

Do not put credentials, private target data, raw connector output or the private
runtime root into the qualification record.

## Non-claims

Qualification is not:

- authorization for production mutation;
- proof that arbitrary profiles, connectors or host adapters are safe;
- proof of exactly-once external side effects;
- a replacement for environment-specific security review;
- a claim that redacted runtime data is automatically public;
- a production-readiness or compliance certification.

## Related

- [Release qualification record](release-qualification-record.md)
- [Known limitations](known-limitations.md)
- [Safety model](safety-model.md)
- [Distribution](distribution.md)
- [Release evidence](release-evidence/README.md)
- [Release security review](release-security-review/README.md)
