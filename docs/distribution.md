# Distribution and installation

RExecOp `0.2.24a0` is the current published alpha line on
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
  - `govengine==0.16.11`
  - `sclite-core==1.0.9`
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
python -m pip install -e /path/to/govengine
python -m pip install "sclite-core==1.0.9"
rm -rf dist build *.egg-info
python -m build
python -m twine check dist/*
python scripts/validate_distribution.py dist
python scripts/validate_supply_chain_gate.py dist
```

## Supply-chain release gate

`package-dry-run` and `publish.yml` run `scripts/validate_supply_chain_gate.py` on built
`dist/` artifacts. The gate:

1. installs the built wheel in an isolated venv,
2. runs `pip-audit` on the frozen dependency tree,
3. writes `dist/rexecop-<version>.cdx.json` (CycloneDX SBOM),
4. fails on vulnerabilities not listed in `docs/supply-chain-audit-exceptions.json`.

Documented audit exceptions use schema `rexecop.supply_chain_audit_exceptions.v0.1`.

### PyPI trusted publishing (preferred)

Long-lived `PYPI_API_TOKEN` upload remains supported in `publish.yml`, but the preferred
path is **PyPI trusted publishing (OIDC)** from GitHub Actions:

1. Configure a trusted publisher for `rozmiarD/RExecOP` on PyPI.
2. Replace the `twine upload` step with `pypa/gh-action-pypi-publish@release/v1`.
3. Keep workflow permissions that allow GitHub OIDC token mint (already enabled).

Do not store upload tokens in the repository, handoffs, or agent memory.

## Install from Git (no local clone)

```bash
python -m pip install "govengine @ git+https://github.com/rozmiarD/GovEngine.git@main"
python -m pip install "rexecop @ git+https://github.com/rozmiarD/RExecOP.git@main"
```

The current RExecOp source line and published `0.2.24a0` wheel require GovEngine `0.16.11` and SCLite `1.0.9`.

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
