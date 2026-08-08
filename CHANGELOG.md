# Changelog

All notable user-visible changes to RExecOp are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Published versions are identified from the public package index. Unpublished
source candidates and milestone-level development notes are preserved in the
[pre-1.0 development archive](docs/archive/pre-1.0-development-history.md).

## Unreleased

### Added

- Alpha `rexecop examples materialize --output NEW_DIR` for the versioned,
  byte-exact first-run fixture packaged in wheel and sdist artifacts. It only
  accepts a new local directory and reports explicit onboarding non-claims;
  it does not initialize a runtime root, execute connectors, make governance
  decisions, emit evidence, or overwrite existing materializations.
- Additive host-configured governed-attempt consumption for `apply` and
  `recovery`: exact GovEngine typed-execution composite admission, approval
  attestation/current revocation, signed decision, atomic attempt claim, and a
  bounded permit/receipt binding. Recovery retains its real mode everywhere
  except the explicit nested typed-execution v0.1 `apply` compatibility alias.
- Explicit profile/environment plugin posture pairs bind `fixture_only` to
  `no_network` and `operator_wrapper` to `local_subprocess`. Policy-bound
  plugins select the optional GovEngine governed-admission v0.2 validator;
  built-ins retain v0.1, and plugin factories remain unloaded until the
  immediate governed pre-I/O boundary.

### Changed

- The current source line now requires exact public `govengine==1.0.0rc2` and
  `sclite-core==2.0.1`. Runtime doctor, release-train preflight, documentation
  and CI fail closed on drift while the already published `rexecop==1.0.0rc1`
  artifact and its historical dependency metadata remain unchanged.
- Ordinary CI sibling checkouts now use reviewed immutable source snapshots
  validated as an exact job, repository, path, and commit-ref multiset. This
  does not auto-update sources, prove package compatibility, or change publish
  and repair workflows.
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
- CI's immutable stack checkouts are advanced to the release-record commits
  `c065d7a157665351054bacc7b5e3ae12b7cc9d98` (SCLite `v2.0.1`) and
  `e65ad22ec25d74bbbb4969bd614981a8ed5e47c8` (GovEngine `v1.0.0rc2`).

### Fixed

- Made admission defer atomic within the exact lease-fenced queue transaction
  and eliminated mixed-backend claim/disposition lifecycle paths by routing all
  built-in queue operations through one store-bound private capability;
  unsupported custom adapters and subclasses now stop before queue mutation or
  filesystem fallback.
- Exact-defer a claimed operation as `target_locked` when final target-lock
  acquisition loses a post-assessment race; later acquisition or persistence
  exceptions now propagate without being relabelled as ordinary contention.
- Redact bounded process identities from private queue transitions when their
  exact value aliases either the prior or current owner token, without storing
  token hashes or adding transition fields.
- Reconciled expired single-host queue claims under one lock and complete fresh
  lease fencing: only a still-approved operation with zero logical-store
  attempts is requeued, while transitioned, attempted, indeterminate, missing,
  malformed or inconsistent cases stop before connector IO with a bounded
  recovery record and stable redacted error.
- Bound direct start, approved `advance` and FIFO drain to the controller's
  private claim-specific path. Compatible bare pending state remains queued and
  byte-identical through public admission; capacity and target contention
  exact-defer the selected claim instead of discarding or replaying it.
- Ordered terminal claim ownership after the durable operation and attempt
  result as target-only release, terminal receipt, exact claim completion and
  trailing drain. A hard receipt failure leaves the claim fenced and suppresses
  drain; existing newer-lease recovery plus repeated terminal cleanup repairs
  it without invoking the connector again. Partial approved `advance` instead
  completes only its admission claim, retains the target lock and emits no
  terminal receipt or trailing drain.
- Ordered cancellation cleanup after a valid durable `cancelled` transition as
  lease-fenced queue removal, target release and trailing drain. Cancellation
  now consistently accepts exactly `waiting_for_approval`, `approved`, `running`
  and `paused`. Public queue mutation and release validate compatible state
  before target release, while fenced or invalid state fails without changing
  queue bytes or target.
- Cleaned up a derived rollback FIFO claim when the existing rollback-authority
  preflight fails: the exact claim is completed and removed with queue metadata
  before the original validation error is re-raised, without connector IO or
  any new rollback execution or retry behavior.
- Replaced permissive YAML ingestion with one bounded, alias-free,
  duplicate-key-safe parser across environment, profile, catalog, action,
  reaction, trigger and secrets boundaries; non-finite values now also fail
  closed before canonical digest or runtime-store persistence with stable,
  redacted reason codes.
- Rejected distinct secret references that collapse to the same legacy
  `REXECOP_SECRET_*` environment key across loaders, doctor inspection and
  per-resolver runtime lookup, while preserving exact file refs and hyphenated
  reference compatibility.
- Made generated secret-reference suggestions fail boundedly on complete-set
  duplicate or environment-key ambiguity instead of inventing replacement names.
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

### Non-claims

- Queue reconciliation does not provide backend exactly-once execution, a
  distributed queue, cross-file ACID, claim renewal or power-loss durability.
- Expired-claim requeue does not repeat an attempt, trigger retry or rollback,
  or resolve pending, attempted or indeterminate work automatically.
- The private controller lifecycle does not change the public `RuntimeStore`
  protocol, queue schemas/results, recovery-report schema or custom-adapter
  contract.
- Governed-attempt mechanics do not enable `mutation_ready`, add a production
  approval/revocation provider, certify live infrastructure, guarantee external
  exactly-once effects, publish a release, or claim that public
  `govengine==1.0.0rc1` already contains the optional source surface.

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
