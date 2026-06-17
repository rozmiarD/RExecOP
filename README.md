# RExecOp

**Governance-bound deterministic operations control-plane for profile-defined workflows.**

Status: **pre-alpha / Phase 8** (external Tecrax profile package integration).

RExecOp is the GovEngine-bound **runner, orchestrator, and executor** for profile-defined workflows. It operationalizes domain profiles while preserving strict layer boundaries.

## Layer boundaries

| Layer | Responsibility |
| --- | --- |
| **SCLite** | Auditable truth: contracts, artifacts, receipts, evidence |
| **GovEngine** | Governance meaning: policy, validation, gates, decisions; runner request/receipt contracts (does not execute operations) |
| **RExecOp** | Operational lifecycle and execution mechanics |
| **Profiles** (Tecrax, …) | Domain semantics: intents, workflows, connectors, validation rules |

Tecrax ships as the external [`tecrax-profile`](https://github.com/rozmiarD/tecrax-profile) package. Ravenclaw is legacy and out of scope.

## What RExecOp is

- A deterministic operations control-plane
- A profile-defined workflow runner and orchestrator
- A producer of SCLite-compatible evidence (future phases)
- A GovEngine-bound execution layer (real adapter in Phase 2B+)

## What RExecOp is not

- A policy engine (GovEngine owns governance)
- A source of truth (SCLite owns auditable artifacts)
- A domain profile (Tecrax/Ravenclaw live outside core)
- Infrastructure automation replacing Ansible, Proxmox, PBS, etc.
- Production-ready software at this stage

## Install (placeholder)

PyPI publishing is reserved for a future release.

```bash
pip install rexecop
```

## Development

```bash
pip install -e ".[dev]"
ruff check .
mypy src/rexecop
pytest
```

## CLI

```bash
rexecop --help
rexecop version
rexecop plan \
  --profile tecrax \
  --env examples/environments/small-public-unit-proxmox.example.yaml \
  --intent check_backup_status \
  --target all_critical_vms \
  --mode dry_run
```

With `tecrax-profile` installed, `--profile tecrax` resolves via entry point.
For offline tests use `examples/profiles/tecrax-fixture/profile.yaml`.

```bash
rexecop status --operation <operation-id>
rexecop history --operation <operation-id>
python -m rexecop --help
```

Runtime artifacts are stored under `.rexecop/` (gitignored).

## Safety

- No real infrastructure connectors in Phase 0
- Real GovEngine integration in Phase 2B (`govengine` dependency); static adapter remains for offline tests
- Static governance adapters are bootstrap/test only, not production policy
- Default future operation modes will favor `dry_run` / `observe` over accidental `apply`

## Roadmap

See the approved roadmap in the operator audit docs. Phase 0 delivers repository bootstrap only. Phase 1+ adds operation core, adapters, and vertical slices.
