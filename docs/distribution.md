# Distribution and installation

RExecOp `0.2.1a0` is **alpha** software. Wheels are validated in CI but **not** published to
public PyPI until explicit operator sign-off.

## Supported install paths

| Path | When to use |
| --- | --- |
| Editable source (`pip install -e`) | Development and operator lab (recommended) |
| Wheel from `dist/` after `python -m build` | Offline install, internal mirrors, release candidates |
| Git URL install | Pin a commit or tag without cloning manually |

Public PyPI is intentionally **out of scope** for alpha. Do not document or claim a PyPI release
until the operator checklist in [OPERATOR_LAB_RUNBOOK.md](../OPERATOR_LAB_RUNBOOK.md) is signed off.

## Prerequisites

- Python **3.11+**
- Network access to install pinned dependencies:
  - `govengine>=0.12.2a0,<0.15`
  - `sclite-core>=1.0.1,<1.1`
- Optional domain profile: [`tecrax`](https://github.com/rozmiarD/tecrax)

## Editable install (recommended)

```bash
git clone https://github.com/rozmiarD/RExecOP.git
cd RExecOP
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

# Optional Tecrax profile package
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

Install the wheel in a clean venv:

```bash
python -m venv /tmp/rexecop-install
source /tmp/rexecop-install/bin/activate
pip install "govengine>=0.12.2a0,<0.15" "sclite-core>=1.0.1,<1.1"
pip install dist/rexecop-*.whl
rexecop version
```

## Install from Git (no local clone)

Pin a branch, tag, or commit:

```bash
python -m pip install "rexecop @ git+https://github.com/rozmiarD/RExecOP.git@main"
```

With Tecrax profile:

```bash
python -m pip install \
  "rexecop[tecrax] @ git+https://github.com/rozmiarD/RExecOP.git@main"
```

When `tecrax` is not yet on a public index, install it separately from Git:

```bash
python -m pip install "tecrax @ git+https://github.com/rozmiarD/tecrax.git@main"
```

## Private index / GitHub Packages (operator-owned)

RExecOp does not publish packages for you. Operators may mirror wheels built by CI or local
`python -m build` into:

- an internal PyPI-compatible index (Artifactory, devpi, etc.)
- [GitHub Packages](https://docs.github.com/en/packages) Python registry

Typical internal publish flow (example — adjust registry URL and credentials):

```bash
python -m build
python -m twine upload --repository-url https://upload.example.internal/legacy/ dist/*
```

Consumer install:

```bash
pip install --index-url https://pypi.example.internal/simple rexecop==0.2.1a0
```

Keep GovEngine and SCLite pins aligned with `pyproject.toml` when mirroring — RExecOp does not
vendor those dependencies.

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
