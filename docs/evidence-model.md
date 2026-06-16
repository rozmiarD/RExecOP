# Evidence model

RExecOp maintains two related but distinct evidence concepts.

## Internal evidence events

Stored under `.rexecop/evidence/<operation_id>/`.

- Append-only JSON events emitted by `EvidenceManager`
- Used for operational history, debugging, and correlation
- Redacted for secret-like fields (`password`, `token`, `api_key`, …)
- **Not** the long-term auditable truth layer

Event types live in `rexecop.evidence.event.EvidenceEventType`.

## SCLite artifacts (authoritative)

Defined and validated by **SCLite** (`sclite-core`).

- Intent, policy, execution contracts/tickets, receipts, evidence contracts
- Digest-linked artifact chain
- Review and replay semantics owned by SCLite

RExecOp will emit these artifacts in Phase 3B. Until then, placeholder exports reference
future schemas only.

## Receipt export (non-authoritative)

Written to `.rexecop/receipts/<operation_id>.json` by `PlaceholderSCLiteEmitter`.

- Summary/export format for operators and tests
- Includes `sclite_schema_ref` per artifact slot
- Marked `authority: non_authoritative_export`
- Must not be treated as a parallel truth schema

## Operation linkage

`Operation.sclite_refs` holds nullable descriptor links per artifact role. In Phase 3A
these are placeholder entries (`status: placeholder`). Phase 3B will populate real
descriptor paths and digests after SCLite emission.

## GovEngine boundary

GovEngine decisions influence which artifacts are required, but GovEngine does not store
SCLite artifacts. RExecOp bridges governance outcomes to SCLite emission at the appropriate
lifecycle boundaries.
