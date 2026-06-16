# SCLite integration

RExecOp does **not** own long-term auditable truth. SCLite does. Phase 3B introduced real
lifecycle emission; Phase 3C upgrades bundles to GovEngine-integration parity with scoped
tickets, receipt-bounded evidence, trust/carrier sidecars, and kernel guard manifests.

## Authority model

| Layer | Role |
| --- | --- |
| **SCLite artifacts** | Authoritative contracts, receipts, evidence bundles (`.rexecop/sclite/<op>/`) |
| **RExecOp internal events** | Runtime telemetry under `.rexecop/evidence/` |
| **RExecOp receipt export** | Summary pointer under `.rexecop/receipts/` (`authority: sclite_artifact`) |

## Phase 3C (current)

Full bundle profile aligned with `sclite/examples/govengine-integration/`:

- Six lifecycle artifacts (`contract-lifecycle-v0.2` roles)
- `execution_ticket.v0.3` scoped ticket with `ticket_use` binding
- Receipt-bounded `evidence_contract` (no live-vuln claims)
- `trust_profile_ref.json` and `carrier_profile_ref.json` sidecars
- `kernel_guard_manifest.json` over `artifact_chain_manifest.json`
- `verify_ticket_use` + `review_bundle` → verdict `pass` on emission
- Explicit `target_host` resolution for scope-fidelity review (logical targets map to `{environment}.fixture`)
- GovEngine admission metadata bridged into `policy_decision.reason_codes` / `risk.reason`

Emitter: `SCLiteArtifactEmitter` in `adapters/sclite_port/emitter.py`  
Full bundle helpers: `adapters/sclite_port/full_bundle.py`  
Placeholder emitter: **deprecated**, offline/bootstrap tests only

## Artifact slots

| Role | Schema |
| --- | --- |
| `intent_contract` | `schemas/intent_contract.v0.2.schema.json` |
| `policy_decision` | `schemas/policy_decision.v0.2.schema.json` |
| `execution_contract` | `schemas/execution_contract.v0.2.schema.json` |
| `execution_ticket` | `schemas/execution_ticket.v0.3.schema.json` |
| `execution_receipt` | `schemas/execution_receipt.v0.2.schema.json` |
| `evidence_contract` | `schemas/evidence_contract.v0.2.schema.json` |
| `trust_profile_ref` | `schemas/trust_profile_ref.v0.1.schema.json` |
| `carrier_profile_ref` | `schemas/carrier_profile_ref.v0.1.schema.json` |
| `kernel_guard_manifest` | `schemas/kernel_guard_hmac_v1.schema.json` |

## Event → artifact mapping

Internal evidence events declare future SCLite mapping via `EVENT_SCLITE_MAPPING` in
`adapters/sclite_port/contracts.py`. Real emission occurs at lifecycle boundaries, not per
internal debug event.

## GovEngine linkage

`policy_decision` and ticket approval status derive from `operation.govengine_decision_type`
and `operation.metadata["govengine_admission"]` when mutating modes are evaluated. Dry-run
operations default to `approved_for_dry_run` scoped ticket approval.

## Dependency

`sclite-core>=1.0.1,<1.1` (aligned with GovEngine alpha pin strategy)

## Boundary

SCLite records auditable truth. RExecOp maps operation lifecycle to SCLite artifact shapes
without forking a parallel receipt schema.
