# Distribution and installation

RExecOp `0.2.3a0` is **alpha** software, published on
[PyPI](https://pypi.org/project/rexecop/0.2.3a0/); maturity limits in
[known-limitations.md](known-limitations.md) still apply.

## Supported install paths

| Path | When to use |
| --- | --- |
| **PyPI** (`pip install rexecop==0.2.3a0`) | Quick evaluation when GovEngine/SCLite pins are acceptable |
| Editable source (`pip install -e`) | Development and operator lab (recommended for contributors) |
| Wheel from `dist/` after `python -m build` | Offline install, internal mirrors |
| Git URL install | Pin a commit or tag without PyPI |

## Prerequisites

- Python **3.11+**
- Network access to install pinned dependencies:
  - `govengine>=0.12.2a0,<0.15`
  - `sclite-core>=1.0.1,<1.1`
- Optional domain profile: [`tecrax`](https://pypi.org/project/tecrax/) or Git

## Install from PyPI

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "rexecop==0.2.3a0"
rexecop version
```

With Tecrax profile (after `tecrax` is on PyPI at a compatible version):

```bash
python -m pip install "rexecop[tecrax]==0.2.3a0"
```

If the `tecrax` extra cannot resolve from PyPI yet, install Tecrax from Git:

```bash
python -m pip install "rexecop==0.2.3a0"
python -m pip install "tecrax @ git+https://github.com/rozmiarD/tecrax.git@main"
```

## Editable install (recommended for development)

```bash
git clone https://github.com/rozmiarD/RExecOP.git
cd RExecOP
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
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
python -m pip install "govengine>=0.12.2a0,<0.15" "sclite-core>=1.0.1,<1.1"
rm -rf dist build *.egg-info
python -m build
python -m twine check dist/*
```

## Install from Git (no local clone)

```bash
python -m pip install "rexecop @ git+https://github.com/rozmiarD/RExecOP.git@main"
```

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
