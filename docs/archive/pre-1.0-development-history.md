# Pre-1.0 development history

This archive preserves source-only candidates and roadmap labels that were
never published as RExecOp package versions. It is historical provenance, not
the current compatibility or release contract.

Published package history remains in [`CHANGELOG.md`](../../CHANGELOG.md).
Current compatibility requirements live in
[`docs/stack-contract-compatibility.md`](../stack-contract-compatibility.md).

## Unpublished 0.3 release candidates

### 0.3.0rc1 — coordinated SCLite/GovEngine candidate

- Bound the source line to SCLite 2.0 and a coordinated GovEngine candidate.
- Moved orchestration graph validation and runtime projection ownership into
  RExecOp while preserving SCLite's canonical contract and verification role.
- Added operator journeys, stack invariants, external-review, supply-chain and
  public-index release gates.

### 0.3.0rc2 — bounded destination-admission candidate

- Bound normalized HTTP scheme, effective port, address class and origin digest
  through typed admission and runtime receipts.
- Kept connector destination metadata from constructing its own allowlist.

### 0.3.0rc3 — GovEngine v1 consumer candidate

- Validated the exact `govengine==1.0.0rc1` and `sclite-core==2.0.0` public
  dependency line before the RExecOp 1.0 release candidate.
- Exercised shared governance conformance, signed decision consumption,
  attempt-bound permits and receipt conformance.
- Hardened GitHub Actions pinning and moved publication to protected OIDC
  Trusted Publishing.

These candidates were source qualification labels. None was published as a
RExecOp `0.3.x` distribution.

## Early roadmap labels

Before the published `0.1.x`/`0.2.x` alpha line, roadmap phases used provisional
labels from `0.3.0a0` through `0.11.0a0`. They tracked development milestones
such as the initial GovEngine adapter, SCLite projection, operation lifecycle,
queue/lock mechanics, external profiles and bounded connectors. They were not a
monotonic package-release sequence and must not be interpreted as published
versions.

The public package versions and dates are listed in the current changelog and
on [PyPI](https://pypi.org/project/rexecop/#history).
