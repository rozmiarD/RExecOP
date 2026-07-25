# Release qualification record

> Operator-controlled record. Do not commit credentials, private target data,
> raw connector output or private runtime artifacts.

| Field | Value |
| --- | --- |
| RExecOp version | `1.0.0rc1` |
| GovEngine version | `1.0.0rc1` (public release candidate) |
| SCLite version | `2.0.0` |
| Tecrax compatibility line | `0.4.0rc3` external downstream consumer; not part of this release |
| Operator | |
| Environment class | |
| Isolated runtime-root identifier | |
| Date (UTC) | |

## Automated checks

| Check | Pass? | Evidence |
| --- | --- | --- |
| `bash scripts/run_alpha_signoff_checks.sh` | [ ] | Aggregate qualification output |
| Full pytest | [ ] | CI or local summary |
| Delivery-scope pytest | [ ] | `pytest -m delivery` |
| GitHub Actions `main` | [ ] | Workflow URL/run id |
| Public-index install smoke | [ ] | `pip check` and validator result |
| External-review gate | [ ] | Versioned review record |
| Release/source binding | [ ] | Tag and source commit |

## Human checks

| # | Item | Pass? | Evidence |
| --- | --- | --- | --- |
| 1 | Operator, lab and safety documents reviewed | [ ] | Initials/date |
| 2 | Lab runbook completed on an isolated root | [ ] | Root identifier only |
| 3 | Exact dependency pins verified | [ ] | Installed versions |
| 4 | Bounded read-only path succeeded | [ ] | Operation id |
| 5 | Public projections reviewed for disclosure | [ ] | Review result |
| 6 | Static/test governance adapter absent | [ ] | Configuration review |
| 7 | Release-candidate limitations accepted | [ ] | Signature |

## Optional read-only evidence

| Field | Value |
| --- | --- |
| Profile | |
| Environment class | fixture / staging |
| Operation id | |
| `doctor` status | |
| Validation result | |

## Signature

Operator acceptance:

```text
name:
date:
scope:
```

## Notes

- The prior `0.2.9a0` alpha sign-off remains archived at
  [archive/alpha-sign-off-record-2026-06-21-0.2.9a0.md](archive/alpha-sign-off-record-2026-06-21-0.2.9a0.md).
- This record is not the independent security-review artifact.
