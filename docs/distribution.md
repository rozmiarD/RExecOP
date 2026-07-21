# Distribution and installation

RExecOp `0.3.0rc3` is the current unpublished coordinated candidate; the
published alpha line remains `0.2.24a0` on
[PyPI](https://pypi.org/project/rexecop/).
The published wheel contains full B2, R4c, watchdog decision truth, and manual recovery records while retaining the maturity limits in
[known-limitations.md](known-limitations.md).

## Supported install paths

| Path | When to use |
| --- | --- |
| **PyPI** (`pip install rexecop==0.2.24a0`) | Evaluation of the single supported alpha line |
| Coordinated editable source (`pip install -e`) | Watchdog-decision truth binding development and operator lab |
| Wheel from `dist/` after `python -m build` | Offline install, internal mirrors |
| Git URL install | Pin a commit or tag without PyPI |

## Prerequisites

- Python **3.11+** (CI on `main` exercises **3.11**, **3.12**, and **3.13**)
- Network access to install pinned dependencies:
  - `govengine==1.0.0rc1`
  - `sclite-core==2.0.0`
- Optional domain profile: [`tecrax`](https://pypi.org/project/tecrax/) or Git

## Install from PyPI

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "rexecop==0.2.24a0"
rexecop version
```

With the compatible Tecrax profile:

```bash
python -m pip install "rexecop[tecrax]==0.2.24a0"
```

## Coordinated editable install

```bash
git clone https://github.com/rozmiarD/RExecOP.git
cd RExecOP
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
git clone https://github.com/rozmiarD/GovEngine.git ../govengine
python -m pip install -e ../govengine
python -m pip install -e ".[dev]"

git clone https://github.com/rozmiarD/tecrax.git ../tecrax
python -m pip install -e ../tecrax

rexecop version
python scripts/validate_public_truth.py
```

## Build a wheel locally

Matches the CI `package-dry-run` job:

```bash
python -m pip install --upgrade pip build twine
mkdir -p /tmp/rexecop-candidate-wheels
python -m build --wheel --outdir /tmp/rexecop-candidate-wheels /path/to/sclite
python -m build --wheel --outdir /tmp/rexecop-candidate-wheels /path/to/govengine
rm -rf dist build *.egg-info
python -m build
python -m twine check dist/*
python scripts/validate_distribution.py dist
python scripts/validate_supply_chain_gate.py dist \
  --candidate-wheel-dir /tmp/rexecop-candidate-wheels
```

## Supply-chain release gate

`package-dry-run` and `publish.yml` run `scripts/validate_supply_chain_gate.py` on built
`dist/` artifacts. The gate:

1. installs the built wheel in an isolated venv,
2. runs `pip-audit` on the frozen dependency tree,
3. writes `dist/rexecop-<version>.cdx.json` (CycloneDX SBOM),
4. fails on vulnerabilities not listed in `docs/supply-chain-audit-exceptions.json`.

Documented audit exceptions use schema `rexecop.supply_chain_audit_exceptions.v0.1`.
For an unpublished release-candidate train, build the exact-pin dependencies into a
local wheelhouse and pass it with `--candidate-wheel-dir`. The isolated install still
resolves and checks the complete wheel environment; it does not require those
candidates to exist on PyPI first.

### PyPI trusted publishing

`.github/workflows/publish.yml` uses **PyPI trusted publishing (OIDC)** from
GitHub Actions. The registered publisher tuple is:

- owner: `rozmiarD`;
- repository: `RExecOP`;
- workflow: `publish.yml`;
- environment: `pypi`;
- PyPI project: `rexecop`.

The GitHub `pypi` environment accepts deployments only from protected refs.
`main` has strict required CI checks, and the active `Protect release tags`
ruleset prevents update or deletion of `v*` tags without a bypass actor. Verify
the live state before publication:

```bash
python scripts/validate_m10_release_gate.py --live-github
```

The official publisher action is pinned to a reviewed full commit SHA. The
workflow carries no long-lived PyPI credential and rejects token-based upload
settings through `scripts/validate_workflow_security.py`.

Do not store upload tokens in the repository, handoffs, or agent memory.

## Install from Git (no local clone)

```bash
python -m pip install "govengine @ git+https://github.com/rozmiarD/GovEngine.git@main"
python -m pip install "rexecop @ git+https://github.com/rozmiarD/RExecOP.git@main"
```

The current RExecOp source candidate requires public GovEngine `1.0.0rc1` and
final public SCLite `2.0.0`; the published RExecOp `0.2.24a0` wheel remains on
the prior public line.

## Private index / GitHub Packages (operator-owned)

Operators may mirror wheels into an internal PyPI-compatible index or GitHub Packages.
See prior internal-mirror examples in git history if needed.

## Version and doc alignment

Before sharing an install artifact outside your host:

```bash
python scripts/validate_public_truth.py
pytest -q
```

See [OPERATOR_RUNBOOK.md](../OPERATOR_RUNBOOK.md) for secrets, staging environments, and
apply safety. See [OPERATOR_LAB_RUNBOOK.md](../OPERATOR_LAB_RUNBOOK.md) for the full
profile → GovEngine → SCLite lab path.

## Related

- [README.md](../README.md) — project overview
- [CHANGELOG.md](../CHANGELOG.md) — release history
- [known-limitations.md](known-limitations.md) — alpha non-claims
