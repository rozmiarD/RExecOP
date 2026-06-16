# SCLite integration (Phase 3A)

RExecOp does **not** own long-term auditable truth. SCLite does. Phase 3A introduces the
port boundary and a **placeholder emitter** that exports non-authoritative receipt summaries
with `sclite_schema_ref` fields for each artifact slot.

## Authority model

| Layer | Role |
| --- | --- |
| **SCLite artifacts** | Authoritative contracts, receipts, evidence bundles |
| **RExecOp internal events** | Runtime/debugging telemetry under `.rexecop/evidence/` |
| **RExecOp receipt export** | Non-authoritative summary under `.rexecop/receipts/` |

Receipt exports include `"authority": "non_authoritative_export"` and are explicitly
bootstrap/offline paths until Phase 3B emits real SCLite artifacts.

## Artifact slots (placeholder)

Each export includes slots for future SCLite artifacts:

- `intent_contract` → `schemas/intent_contract.v0.2.schema.json`
- `policy_decision` → `schemas/policy_decision.v0.2.schema.json`
- `execution_contract` → `schemas/execution_contract.v0.2.schema.json`
- `execution_ticket` → `schemas/execution_ticket.v0.2.schema.json`
- `execution_receipt` → `schemas/execution_receipt.v0.2.schema.json`
- `evidence_contract` → `schemas/evidence_contract.v0.2.schema.json`

## Event → future schema mapping

Internal evidence events declare their future SCLite mapping via
`EVENT_SCLITE_MAPPING` in `adapters/sclite_port/contracts.py`.

| Internal event | Future SCLite artifact |
| --- | --- |
| `operation_created` | `intent_contract` |
| `plan_generated` | `execution_contract` |
| `govengine_decision_*` | `policy_decision` |
| `approval_received` | `execution_ticket` |
| `state_transition`, `step_*`, `receipt_generated`, `operation_completed/failed` | `execution_receipt` |
| `validation_*`, `operation_escalated` | `evidence_contract` |

## Phase 3B (next)

Real emission via `adapters/sclite_port/emitter.py` will populate `operation.sclite_refs`
with descriptor links to validated SCLite artifacts.
