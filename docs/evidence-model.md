# Evidence model

RExecOp maintains two related but distinct evidence concepts.

## Internal evidence events

**Location:** `.rexecop/evidence/<operation_id>/`

- Append-only JSON events emitted by `EvidenceManager`
- Used for operational history, debugging, and correlation
- Redacted for secret-like fields (`password`, `token`, `api_key`, `authorization`, …)
- **Not** the long-term auditable truth layer

Event types are defined in `rexecop.evidence.event.EvidenceEventType`. Every event type
declares its SCLite mapping target in `adapters/sclite_port/contracts.py` (`EVENT_SCLITE_MAPPING`).

Representative events:

| Event | Typical trigger |
| --- | --- |
| `operation_created` | `plan` |
| `plan_generated` | plan materialized |
| `state_transition` | any valid state change |
| `govengine_decision_requested` / `govengine_decision_received` | mutating plan/start |
| `step_started` / `step_completed` / `step_failed` | workflow execution |
| `validation_started` / `validation_completed` | profile validation |
| `receipt_generated` | SCLite export path |
| `operation_completed` / `operation_failed` / `operation_escalated` | terminal paths |

## SCLite artifacts (authoritative)

**Location:** `.rexecop/sclite/<operation_id>/`

Defined and validated by **SCLite** (`sclite-core`). Emitted by `SCLiteArtifactEmitter` on the
completion path with full GovEngine-integration bundle parity (see [sclite-integration.md](sclite-integration.md)).

- Intent, policy, execution contracts/tickets, receipts, evidence contracts
- Digest-linked artifact chain and kernel guard manifest
- Review semantics owned by SCLite (`review_bundle`, `verify_ticket_use`)

`Operation.sclite_refs` holds descriptor links per artifact role after successful emission.

## Receipt export (non-authoritative)

**Location:** `.rexecop/receipts/<operation_id>.json`

Written as an operator summary export after bundle emission.

- Points at SCLite descriptors under `.rexecop/sclite/`
- Includes GovEngine decision summary and validation outcome
- Must not be treated as a parallel truth schema when SCLite bundles exist

The deprecated `PlaceholderSCLiteEmitter` path remains for offline tests only.

## Connector and API payloads

Connector responses (including `http_api` JSON) pass through `redact_payload()` before
persistence in evidence or step results. Environment YAML must use `secret_ref` — inline
secrets are rejected at load time (`environment/sanitize.py`).

## GovEngine boundary

GovEngine decisions influence which artifacts are required and how `policy_decision` is populated,
but GovEngine does not store SCLite artifacts. RExecOp bridges governance outcomes to SCLite
emission at lifecycle boundaries.

## Operation linkage

```text
Operation
  evidence_event_ids[]     -> internal events
  sclite_refs{}            -> SCLite descriptor links
  metadata.shared_state    -> workflow correlation (validation input)
  metadata.validation      -> last declarative validation result
  metadata.govengine_admission -> admission snapshot for SCLite bridge
```
