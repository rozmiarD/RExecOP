# SCLite integration and evidence model

RExecOp produces runtime records and projects completed operation lifecycles
into contracts verified with SCLite. It does not replace SCLite's canonical
evidence contracts or verification semantics, and SCLite does not execute or
persist RExecOp operations.

Exact dependency:

```text
sclite-core==2.0.0
```

## Ownership and non-claims

SCLite owns:

- canonicalization and digest rules;
- lifecycle, ticket, receipt and evidence contract shapes;
- bundle and ticket-use verification;
- review-bundle semantics.

RExecOp owns:

- runtime operations, attempts and internal events;
- storage and emission timing;
- projection of runtime facts into supported SCLite contracts;
- observation, finding, reaction, escalation, trigger, watchdog and
  automation-chain runtime semantics and schema resources.

The RExecOp-owned orchestration resources are resolved by
`ORCHESTRATION_SCHEMA_RESOLVER` under `rexecop.io/*@v0.1`. Historical
`schemas/*.v0.1.schema.json` references remain embedded for compatibility.
They do not transfer ownership to SCLite.

Neither an internal event nor a receipt export is an alternative authoritative
truth record. Emitting a valid bundle also does not prove that an external
system reached the operator's intended real-world state.

## Runtime evidence layers

Paths use the selected runtime root from `--root`, `REXECOP_ROOT`, `--instance`,
`REXECOP_INSTANCE`, or `./.rexecop`.

| Layer | Location | Role |
| --- | --- | --- |
| Internal events | `<root>/evidence/<operation_id>/` | Bounded runtime telemetry, history and correlation |
| SCLite-compatible bundle | `<root>/sclite/<operation_id>/` | Canonical lifecycle/evidence contracts and verification inputs |
| Receipt export | `<root>/receipts/<operation_id>.json` | Non-authoritative operator summary and descriptor pointers |

`Operation.evidence_event_ids` links internal events.
`Operation.sclite_refs` links emitted artifact descriptors.

## Internal events

`EvidenceManager` emits append-only JSON records for lifecycle and diagnostic
use. Representative event types include:

| Event | Trigger |
| --- | --- |
| `operation_created` | Operation planning starts |
| `plan_generated` | `OperationPlan` is persisted |
| `state_transition` | Lifecycle state changes |
| `govengine_decision_requested` / `govengine_decision_received` | Mutating governance path |
| `step_started` / `step_completed` / `step_failed` | Workflow execution |
| `validation_started` / `validation_completed` | Profile validation |
| `receipt_generated` | Bundle/receipt projection |
| terminal operation events | Completion, failure or escalation |

Events are bounded and redacted using forbidden-key checks, known token
patterns and exact values resolved by the current process. These controls reduce
exposure; they do not make the runtime root safe to publish without operator
review.

## Bundle emission

The primary implementation is `SCLiteArtifactEmitter` in
`adapters/sclite_port/emitter.py`, with bundle helpers in
`adapters/sclite_port/full_bundle.py`.

The current lifecycle slots are:

| Role | Schema |
| --- | --- |
| `intent_contract` | `intent_contract.v0.2` |
| `policy_decision` | `policy_decision.v0.3` |
| `execution_contract` | `execution_contract.v0.3` |
| `execution_ticket` | `execution_ticket.v0.3` |
| `execution_receipt` | `execution_receipt.v0.2` |
| `evidence_contract` | `evidence_contract.v0.2` |
| `trust_profile_ref` | `trust_profile_ref.v0.1` |
| `carrier_profile_ref` | `carrier_profile_ref.v0.1` |
| `kernel_guard_manifest` | Optional HMAC guard or `not_required` |

Emission also:

- resolves an explicit `target_host` for scope fidelity;
- projects available GovEngine decision metadata into `policy_decision`;
- verifies ticket use and the resulting review bundle;
- records descriptor references back on the operation.

`REXECOP_KERNEL_GUARD_KEY`, when used, must contain at least 32 UTF-8 bytes.
SCLite enforces the supported type and length; neither project claims to
measure key entropy. Key custody remains host-owned.

Read-only operations use scoped dry-run/review ticket defaults. Mutating paths
bind their GovEngine decision metadata. The explicit `legacy_read_only` path
does not gain signed governance authenticity merely because its lifecycle is
projected into a valid SCLite bundle.

## Receipt and audit projections

The receipt export points to bundle descriptors and summarizes governance and
validation outcomes. It must not be treated as canonical when the corresponding
bundle exists.

Operator commands provide bounded views over persisted state:

| Command | Projection |
| --- | --- |
| `receipt show` | Receipt export, descriptor references and digest status |
| `evidence show` | Bounded internal events and sensitivity summary |
| `chain summary` | Operation, evidence, reaction and bundle linkage |
| `chain explain` | Read-only explanation of the persisted truth path |
| `reaction explain` | Reaction and automation-chain verification summary |
| `support bundle --redacted` | Bounded diagnostic package for review or support |

These commands create projections, not new truth artifacts. They are designed
to avoid raw connector output and secret resolution, but redaction is not a
publication guarantee. Review exported data before sharing it.

Connector results pass through supported redaction and output-bounding
functions before persistence. Shell connectors store bounded stdout/stderr and
full-output SHA-256 digests. Environment configuration must use `secret_ref`;
inline secret values are rejected during planning.

See [Execution contract](execution-contract.md) and
[Secrets](secrets-operator.md).

## Deprecated path

`PlaceholderSCLiteEmitter` and
`OperationController.export_placeholder_receipt()` remain for deprecated
offline/bootstrap tests. Their JSON output is not a canonical long-term
evidence path.

## Conformance

Release candidates use `scripts/validate_f4_conformance_matrix.py` with a fixed
local wheelhouse and exact candidate artifact digests. Stack import and contract
compatibility are additionally checked by `scripts/validate_stack_contracts.py`.
