# Deterministic Reaction Interpreter

RExecOp compiles a bounded reaction pack supplied by the selected profile. The
core understands only paths, fixed comparison operators, stable priority, and
four outcomes: `run_intent`, `retry_intent`, `escalate`, and `no_op`. Finding
taxonomy, thresholds, summaries, and intent selection remain profile-owned.

## Runtime boundary

```text
SCLite observation envelope
  -> profile reaction pack
  -> deterministic RExecOp evaluation
  -> GovEngine PolicyEngine admission
  -> optional GovEngine automation-transition admission
  -> normal RExecOp operation lifecycle
  -> SCLite receipt, reaction chain and automation_chain projection
```

An executable candidate must resolve to a read-only intent in the same profile
snapshot. RExecOp requires an environment policy pack and accepts only a plain
GovEngine `allow` with no obligations, constraints, or blockers. All other
results become a non-executable escalation.

The compiler limits pack size, rule count, condition count, operator set, path
shape, depth, total reactions, and visited rule digests. Unknown fields,
mutating intents, cycles, exhausted budgets, and profile digest drift fail
closed.

## CLI

```bash
rexecop reaction-plan \
  --profile tecrax \
  --env /path/outside/repo/environment.yaml \
  --observation observation.json \
  --target monitoring-host-01

rexecop reaction-plan \
  --profile tecrax \
  --env /path/outside/repo/environment.yaml \
  --operation op-source \
  --target monitoring-host-01

rexecop reaction-start --reaction reaction-...
rexecop reaction-replay --reaction reaction-...
rexecop reaction explain --reaction reaction-...
rexecop reaction-proposal-validate --profile tecrax --proposal proposal.json
```

`reaction-plan` never starts the child operation. `reaction-start` can start
only the already admitted child and uses the ordinary connector, validation,
evidence, and receipt path. `reaction-replay` performs no execution.
`reaction explain` verifies the persisted reaction-chain manifest and emits a
bounded, redacted operator projection; SCLite remains the truth authority. When
the reaction planned a child operation, RExecOp also writes
`05_automation_chain.json` using SCLite `automation_chain.v0.1`. If the
installed GovEngine line exposes `AutomationTransitionRequest`, the child edge
stores the GovEngine automation admission digest; otherwise the binding reports
`unavailable` without claiming a GovEngine digest.

Operation creation also supports an explicit plan-only automation mode:

```bash
rexecop plan \
  --profile tecrax \
  --env /path/outside/repo/environment.yaml \
  --intent diagnose_monitoring_host \
  --target monitoring-host-01 \
  --mode dry_run \
  --auto-react plan_only
```

`auto-react plan_only` is evaluated only after the source operation reaches
`completed` and validation passes. It loads the profile-produced
`metadata.shared_state.reaction_observation`, runs the same deterministic
reaction planner, writes the replayable reaction chain, and may create an
admitted child operation in `planned` state. It never starts that child
operation and it is ignored unless explicitly requested on the source
operation. `trigger` accepts the same `auto_react` value from JSON/inbox
payloads.

When the source operation was planned through a target catalog, reaction-planned
child operations use the same catalog path and catalog target id. The child plan
therefore keeps `catalog_binding` / `catalog_runtime` and fails closed on catalog
applicability or digest drift before any child operation is created.

`reaction-plan` accepts exactly one observation source:

- `--observation` points at an already generated SCLite
  `observation_envelope.v0.1` JSON file.
- `--operation` loads `metadata.shared_state.reaction_observation` from a
  completed source operation.

RExecOp does not construct profile facts or domain observations. The selected
profile must produce the observation envelope. RExecOp only validates the SCLite
schema, selected profile id/version/digest, source operation binding, and target
binding before evaluating the profile-owned reaction pack.

Profiles that want to use `--operation` should declare a neutral
`reaction_observation` block in the producing intent metadata. RExecOp's
`validate_profile_conformance()` checks the shared-state key, SCLite schema ref,
source intent, completed-operation requirement, producer workflow step, operation
catalog projection, connector contracts, and reaction pack shape. It does not
interpret finding taxonomy or profile facts.

## LLM boundary

An LLM may produce only `escalation_proposal.v0.1`. The schema rejects commands
and executable payloads and declares `may_execute=false`. Validation proves only
shape and profile intent compatibility; it does not authorize the proposal.
Any future acceptance path must repeat GovEngine admission and create a normal
operation. No LLM adapter has connector or executor access.
