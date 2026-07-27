# Changelog

All notable user-visible changes to RExecOp are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Published versions are identified from the public package index. Unpublished
source candidates and milestone-level development notes are preserved in the
[pre-1.0 development archive](docs/archive/pre-1.0-development-history.md).

## Unreleased

### Changed

- Reworked README and operator documentation around the verified execution
  kernel, stable read-only posture, explicit claims/non-claims and actual
  GovEngine/SCLite ownership boundaries.
- Consolidated the evidence model into the SCLite integration document and
  moved historical qualification material out of current compatibility docs.
- Renamed alpha sign-off documents to release qualification while preserving
  the compatibility name of `scripts/run_alpha_signoff_checks.sh`.
- The publish workflow checks out immutable tagged source before build and
  installs release-validation dependencies into that source environment.
- PyPI staging now contains only the wheel and sdist; SBOM and evidence remain
  GitHub Release assets.
- GitHub Release prerelease status is derived from the validated PEP 440
  version in both publish and repair workflows.
- Runtime permits, leases, fencing and attempt bindings are revalidated after
  durable attempt creation and immediately before connector I/O.

### Fixed

- Prevented non-watchdog inbox failures from repeatedly renaming and
  reprocessing themselves by quarantining them under fixed, no-overwrite names;
  quarantine or required-log failures now stop later inbox and queue work.
- Preserved reached watchdog retry state when the governed final dead-letter
  move fails, capped its retry count for repair attempts, and clear it only after
  the move and required record/projection persistence succeed.
- Reject symlinked inbox and dead-letter directory topology before permissions
  are normalized, and revalidate source identity immediately before quarantine.
- Bound watchdog record/projection persistence failures after dead-letter
  containment without falsely clearing the capped retry state.
- Clarified that GovEngine owns attempt-bound governance authorization while
  RExecOp owns the runtime permit, lease and fencing enforcement.
- Removed universal-GovEngine-admission wording for the explicit
  `legacy_read_only` path.
- Corrected ownership of observation, finding, reaction, trigger, watchdog and
  automation-chain contracts to RExecOp.
- Removed copy-and-paste mutation instructions from the stable operator
  runbook.
- Corrected release-evidence documentation: Tecrax is a downstream consumer,
  not part of the RExecOp evidence inventory.
- Made source-bound review records fail closed on invalid versions, legacy
  schemas and any release-commit delta beyond the review record itself.

## [1.0.0rc1] - 2026-07-25

### Added

- Stable `rexecop.public_api.v1` manifest with 23 `stable_v1` CLI commands and
  explicit alpha classification for the remaining command surface.
- Certified single-host `FileStore` runtime mechanics: atomic FIFO claims,
  operation CAS, fenced execution leases, durable attempts, outbox
  reconciliation and deterministic `outcome_indeterminate` recovery.
- Attempt-bound pre-I/O runtime permits and signed GovEngine decision
  consumption with runtime, lease, fencing, scope, inventory and nonce
  bindings.
- Receipt conformance binding GovEngine decisions and runtime attempts to the
  existing SCLite `execution_receipt.v0.2` extension point.
- Versioned release evidence binding tagged source, wheel, sdist, CycloneDX
  SBOM and GitHub provenance attestations.
- Runtime readiness, public API, supply-chain, clean-install, operational
  qualification and independent-review gates.

### Changed

- Moved observation, finding, reaction, escalation, trigger, watchdog and
  automation-chain schema resources and semantic verification into RExecOp.
  SCLite remains the canonical evidence-contract and verification kernel.
- Pinned the public stack baseline to `govengine==1.0.0rc1` and
  `sclite-core==2.0.0`.
- Removed Tecrax from RExecOp package extras and publication evidence. Tecrax
  remains a separately versioned downstream profile consumer.
- Restricted stable HTTP execution to declared network-policy posture and
  bounded destination bindings.
- Added bounded public/support/runtime audiences with exact-path projection
  allowlists.

### Security

- The default `stable_read_only` posture rejects `apply` and `recovery` before
  execution and again before connector I/O.
- Mutating execution has no unsigned decision fallback.
- Capability requirements come from profile-owned execution requirements, not
  backend self-description.
- Connector destinations cannot construct their own allowlists.
- Stale decisions, attempts, leases, fencing tokens, inventories and reused
  nonces fail closed with stable reason codes.
- GitHub Actions are pinned to reviewed commit SHAs; publication uses protected
  OIDC Trusted Publishing.

### Non-claims

- The release candidate does not certify production mutation, distributed
  execution, arbitrary plugins/connectors or exactly-once external side
  effects.
- `legacy_read_only` does not carry signed per-attempt governance
  authenticity.
- Runtime redaction does not make the private runtime root safe to publish.

## Published alpha history

These versions were published to PyPI. Their detailed development notes,
including historical terminology and source-only candidates, are retained in
the [archive](docs/archive/pre-1.0-development-history.md).

| Version | Published |
| --- | --- |
| [`0.2.24a0`](https://pypi.org/project/rexecop/0.2.24a0/) | 2026-07-05 |
| [`0.2.23a0`](https://pypi.org/project/rexecop/0.2.23a0/) | 2026-07-05 |
| [`0.2.22a0`](https://pypi.org/project/rexecop/0.2.22a0/) | 2026-07-05 |
| [`0.2.21a0`](https://pypi.org/project/rexecop/0.2.21a0/) | 2026-07-05 |
| [`0.2.20a0`](https://pypi.org/project/rexecop/0.2.20a0/) | 2026-07-05 |
| [`0.2.19a0`](https://pypi.org/project/rexecop/0.2.19a0/) | 2026-07-05 |
| [`0.2.18a0`](https://pypi.org/project/rexecop/0.2.18a0/) | 2026-07-05 |
| [`0.2.17a0`](https://pypi.org/project/rexecop/0.2.17a0/) | 2026-07-05 |
| [`0.2.16a0`](https://pypi.org/project/rexecop/0.2.16a0/) | 2026-07-04 |
| [`0.2.15a0`](https://pypi.org/project/rexecop/0.2.15a0/) | 2026-07-04 |
| [`0.2.14a0`](https://pypi.org/project/rexecop/0.2.14a0/) | 2026-07-04 |
| [`0.2.13a0`](https://pypi.org/project/rexecop/0.2.13a0/) | 2026-07-04 |
| [`0.2.12a0`](https://pypi.org/project/rexecop/0.2.12a0/) | 2026-07-04 |
| [`0.2.11a0`](https://pypi.org/project/rexecop/0.2.11a0/) | 2026-06-29 |
| [`0.2.9a0`](https://pypi.org/project/rexecop/0.2.9a0/) | 2026-06-28 |
| [`0.2.8a0`](https://pypi.org/project/rexecop/0.2.8a0/) | 2026-06-28 |
| [`0.2.7a0`](https://pypi.org/project/rexecop/0.2.7a0/) | 2026-06-27 |
| [`0.2.6a0`](https://pypi.org/project/rexecop/0.2.6a0/) | 2026-06-24 |
| [`0.2.5a0`](https://pypi.org/project/rexecop/0.2.5a0/) | 2026-06-22 |
| [`0.2.4a0`](https://pypi.org/project/rexecop/0.2.4a0/) | 2026-06-20 |
| [`0.2.3a0`](https://pypi.org/project/rexecop/0.2.3a0/) | 2026-06-20 |
| [`0.2.2a0`](https://pypi.org/project/rexecop/0.2.2a0/) | 2026-06-20 |

[1.0.0rc1]: https://github.com/rozmiarD/RExecOP/releases/tag/v1.0.0rc1
