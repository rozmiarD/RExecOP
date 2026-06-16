# RExecOp

**Governance-bound deterministic operations control-plane for profile-defined workflows.**

Status: **pre-alpha / bootstrap (Phase 0)**. This repository is an early skeleton only.

RExecOp is the GovEngine-bound **runner, orchestrator, and executor** for profile-defined workflows. It operationalizes domain profiles while preserving strict layer boundaries.

## Layer boundaries

| Layer | Responsibility |
| --- | --- |
| **SCLite** | Auditable truth: contracts, artifacts, receipts, evidence |
| **GovEngine** | Governance meaning: policy, validation, gates, decisions; runner request/receipt contracts (does not execute operations) |
| **RExecOp** | Operational lifecycle and execution mechanics |
| **Profiles** (Tecrax, Ravenclaw, …) | Domain semantics: intents, workflows, connectors, validation rules |

## What RExecOp is

- A deterministic operations control-plane
- A profile-defined workflow runner and orchestrator
- A producer of SCLite-compatible evidence (future phases)
- A GovEngine-bound execution layer (adapter in later phases)

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

## CLI (Phase 0)

```bash
rexecop --help
rexecop version
python -m rexecop --help
```

## Safety

- No real infrastructure connectors in Phase 0
- No real GovEngine or SCLite integration in Phase 0
- Static governance adapters (future) are bootstrap/test only, not production policy
- Default future operation modes will favor `dry_run` / `observe` over accidental `apply`

## Roadmap

See the approved roadmap in the operator audit docs. Phase 0 delivers repository bootstrap only. Phase 1+ adds operation core, adapters, and vertical slices.
