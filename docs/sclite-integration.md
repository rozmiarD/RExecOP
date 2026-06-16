# SCLite integration

RExecOp does **not** own long-term auditable truth. SCLite does. Phase 3B emits validated
SCLite v0.2 lifecycle artifacts; Phase 3A placeholder export remains for offline tests only.

## Authority model

| Layer | Role |
| --- | --- |
| **SCLite artifacts** | Authoritative contracts, receipts, evidence bundles (`.rexecop/sclite/<op>/`) |
| **RExecOp internal events** | Runtime telemetry under `.rexecop/evidence/` |
| **RExecOp receipt export** | Summary pointer under `.rexecop/receipts/` (`authority: sclite_artifact`) |

## Phase 3B (current)

- Real emitter: `SCLiteArtifactEmitter` in `adapters/sclite_port/emitter.py`
- Intent emission at plan/governance boundary (`01_intent_contract.json`)
- Full lifecycle bundle via `controller.export_receipt()` using `materialize_review_bundle()`
- `validate_review_bundle_shape()` sanity check on emitted bundles
- `operation.sclite_refs` populated with descriptor paths + digests
- Placeholder emitter: **deprecated**, offline/bootstrap tests only

## Artifact slots

| Role | Schema |
| --- | --- |
| `intent_contract` | `schemas/intent_contract.v0.2.schema.json` |
| `policy_decision` | `schemas/policy_decision.v0.2.schema.json` |
| `execution_contract` | `schemas/execution_contract.v0.2.schema.json` |
| `execution_ticket` | `schemas/execution_ticket.v0.2.schema.json` |
| `execution_receipt` | `schemas/execution_receipt.v0.2.schema.json` |
| `evidence_contract` | `schemas/evidence_contract.v0.2.schema.json` |

## Event → artifact mapping

Internal evidence events declare future SCLite mapping via `EVENT_SCLITE_MAPPING` in
`adapters/sclite_port/contracts.py`. Real emission occurs at lifecycle boundaries, not per
internal debug event.

## GovEngine linkage

`policy_decision` and ticket approval status derive from `operation.govengine_decision_type`
when mutating modes are evaluated. Dry-run operations default to integrity-only dry-run ticket
approval.

## Dependency

`sclite-core>=1.0.1,<1.1` (aligned with GovEngine alpha pin strategy)

## Boundary

SCLite records auditable truth. RExecOp maps operation lifecycle to SCLite artifact shapes
without forking a parallel receipt schema.
