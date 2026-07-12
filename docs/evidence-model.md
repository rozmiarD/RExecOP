# Evidence model

RExecOp maintains two related but distinct evidence concepts.

Paths below use `<root>/` for the selected runtime root (`--root`, `REXECOP_ROOT`,
named `--instance`, or fallback `./.rexecop`).

## Internal evidence events

**Location:** `<root>/evidence/<operation_id>/`

- Append-only JSON events emitted by `EvidenceManager`
- Used for operational history, debugging, and correlation
- Redacted by key, known provider-token patterns and exact values resolved by the process
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

**Location:** `<root>/sclite/<operation_id>/`

Defined and validated by **SCLite** (`sclite-core`). Emitted by `SCLiteArtifactEmitter` on the
completion path with full GovEngine-integration bundle parity (see [sclite-integration.md](sclite-integration.md)).

- Intent, policy, execution contracts/tickets, receipts, evidence contracts
- Digest-linked artifact chain and kernel guard manifest
- Review semantics owned by SCLite (`review_bundle`, `verify_ticket_use`)

`Operation.sclite_refs` holds descriptor links per artifact role after successful emission.

## Receipt export (non-authoritative)

**Location:** `<root>/receipts/<operation_id>.json`

Written as an operator summary export after bundle emission.

- Points at SCLite descriptors under `<root>/sclite/`
- Includes GovEngine decision summary and validation outcome
- Must not be treated as a parallel truth schema when SCLite bundles exist

The deprecated `PlaceholderSCLiteEmitter` path remains for offline tests only.

## Audit CLI projections

M7 audit commands expose operator-facing projections over the existing runtime store:

| Command | Schema | Purpose |
| --- | --- | --- |
| `receipt show OPERATION_ID` | `rexecop.receipt_show.v0.1` | Redacted receipt export and SCLite descriptor refs with missing/broken digest status |
| `evidence show OPERATION_ID` | `rexecop.evidence_show.v0.1` | Bounded internal evidence events plus sensitivity summary |
| `chain summary OPERATION_ID` | `rexecop.chain_summary.v0.1` | Operation/evidence/reaction/SCLite digest-link summary |
| `chain explain OPERATION_ID` | `rexecop.chain_explain.v0.1` | Truth-path and reaction replay explanation without execution |
| `reaction explain --reaction ID` | `rexecop.reaction_explain.v0.1` | Persisted reaction-chain and automation-chain verification summary |
| `support bundle OPERATION_ID --redacted` | `rexecop.support_bundle.v0.1` | Redacted diagnostic bundle for handoff/support |

These commands do not create new truth artifacts, do not read secrets and do not print raw
connector output. `support bundle` deliberately requires `--redacted`.
The bundle declares `audience: support_bundle` and applies a separate bounded allowlist;
it does not reuse the broader runtime-local operator view. Structured logs declare
`audience: runtime_diagnostic`, while connector public evidence follows the
`public_shareable` contract.

## Connector and API payloads

Connector responses (including `http_api` JSON) pass through `redact_payload()` before
persistence in evidence or step results. Shell backends (`local_shell_readonly`, `ssh_readonly`)
also cap stored stdout/stderr via `bounded_text()` and attach full-output SHA-256 digests —
see [execution-contract.md](execution-contract.md). Environment YAML must use `secret_ref` — inline
secrets are rejected across the complete environment at plan time (`environment/sanitize.py`).
Redaction is repeated at connector dispatch, step execution and evidence persistence boundaries.

## GovEngine boundary

GovEngine decisions influence which artifacts are required and how `policy_decision` is populated,
but GovEngine does not store SCLite artifacts. RExecOp bridges governance outcomes to SCLite
emission at lifecycle boundaries.

## Operation linkage

```text
Operation
  evidence_event_ids[]     -> internal events
  sclite_refs{}            -> SCLite descriptor links
  metadata.shared_state    -> workflow correlation (validation input);
                              execution_request / execution_receipt (runtime contracts)
  metadata.validation      -> last declarative validation result
  metadata.govengine_admission -> admission snapshot for SCLite bridge
  metadata.auto_reaction.automation_admission -> optional GovEngine automation transition digest
```
