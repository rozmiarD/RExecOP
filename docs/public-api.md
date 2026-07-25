# Public API and compatibility policy

RExecOp `1.0.0rc1` freezes the Python and CLI compatibility surface for
the 1.x line. This is an API compatibility decision, not a production
readiness claim; security, release and operational M10 gates remain separate.

The machine-readable source of truth is
`rexecop.public_api.public_api_manifest()` (`rexecop.public_api.v1`). The M10
gate imports every listed symbol in a fresh Python subprocess and rejects any
unclassified CLI command.

## Supported Python imports

Only the symbols listed in `SUPPORTED_PUBLIC_IMPORTS` carry the 1.x compatibility
promise. The supported modules are intentionally narrow:

| Module | Supported purpose |
| --- | --- |
| `rexecop` | Package version |
| `rexecop.connectors` | Connector request, response and runtime protocol |
| `rexecop.contracts.orchestration` | RExecOp-owned reaction, trigger, watchdog and automation contracts |
| `rexecop.evidence` | Evidence event type and bounded redaction helper |
| `rexecop.execution` | Typed execution and profile normalizer contracts |
| `rexecop.profile` | Profile loading, validation and entry-point resolution |
| `rexecop.reaction` | Bounded deterministic reaction compiler/evaluator |
| `rexecop.errors` | Typed public RExecOp exceptions |
| `rexecop.public_api` | This manifest and import matrix |

For the exact symbol list, run:

```python
from rexecop.public_api import public_api_manifest

print(public_api_manifest()["python_api"])
```

Deep modules and symbols absent from this matrix are implementation details or
alpha extension points. In particular, operation controllers, runtime
coordination and storage implementations remain internal even when a package
`__init__` keeps a cycle-safe convenience import for current source consumers.

## CLI stability

The 23 commands returned under `cli.stable_commands` are the supported
`stable_v1` JSON surfaces and are all present in `CLI_CONTRACTS`. Their success
schema, formats, exit-code policy and authority boundary are exposed by
`rexecop contracts cli`.

Every other installed CLI leaf is listed under `cli.alpha_commands`. Those
commands remain usable, but their output shape and flags do not carry the 1.x
compatibility promise. A new command that is neither registered as stable nor
listed as alpha/internal fails `validate_m10_public_api_gate.py`.

## Schema compatibility

RExecOp uses `unknown_major_fail_closed`: a supported schema version is accepted
exactly; an unknown version or major is rejected. Compatibility is not inferred
from similarly shaped JSON.

## Runtime-root upgrade decision

The policy is `alpha_root_requires_new_v1_root`: an alpha runtime root is not
upgraded in place to 1.0. A 1.x process encountering
a manifest written by a `0.x` RExecOp returns
`runtime_root_new_root_required` before `init` can overwrite it. Operators must:

1. keep the alpha root read-only for audit/reference;
2. create a new empty root with the 1.x binary;
3. re-plan supported read-only work in the new root;
4. move no queue, lease, attempt or lifecycle state between roots.

This avoids pretending that alpha runtime state has a stable migration contract.
Backup/restore remains valid only within a compatible runtime-root major line.

## Gate

```bash
python scripts/validate_m10_public_api_gate.py
```

The gate covers the fresh-subprocess import matrix, complete CLI classification,
schema fail-closed behavior and the alpha-to-1.0 new-root decision.
