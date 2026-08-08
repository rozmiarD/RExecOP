# Distribution and installation

RExecOp `1.0.0rc1` is the stable read-only release candidate published through
the protected OIDC workflow on [PyPI](https://pypi.org/project/rexecop/).
The wheel contains the versioned public subset, runtime implementation,
packaged contract/schema resources, and the versioned first-run fixture used by
`rexecop examples materialize --output NEW_DIR`. Qualification and release
evidence remain repository/GitHub Release records, not claims embedded by
installing the wheel.
See [Known limitations](known-limitations.md).

## Supported install paths

| Path | When to use |
| --- | --- |
| **PyPI** (`pip install rexecop==1.0.0rc1`) | Evaluation of the stable read-only release candidate |
| Coordinated editable source (`pip install -e`) | Development, cross-repository integration and operator lab |
| Wheel from `dist/` after `python -m build` | Offline install, internal mirrors |
| Git URL install | Pin a commit or tag without PyPI |

## Prerequisites

- Python **3.11+** (CI on `main` exercises **3.11**, **3.12**, and **3.13**)
- Network access to install pinned dependencies:
  - `govengine==1.0.0rc1`
  - `sclite-core==2.0.0`
- Optional domain profile consumer: [`tecrax`](https://github.com/rozmiarD/tecrax),
  installed and released separately

## Install from PyPI

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "rexecop==1.0.0rc1"
rexecop version
```

Tecrax is not a RExecOp extra. For coordinated source integration:

```bash
git clone https://github.com/rozmiarD/tecrax.git ../tecrax
python -m pip install -e ../tecrax
```

This keeps the public RExecOp core install independent of a domain package
release. Tecrax remains an external source consumer and plugin.

## Diagnose an incompatible plugin install

`rexecop doctor` reports a structured `plugin_posture` blocker with
`reason_code: plugin_incompatible` when a connector or internal-action plugin
cannot be diagnosed compatibly. The blocker includes bounded, redacted
`incompatible_plugins` evidence; a stale profile entry-point load error likewise
shows only the bounded profile identity, stable failure reason and exception
class. Enumeration, entry-point name access, load, entry-point invocation,
loaded-result conversion, path expansion/resolution, path validation and
non-directory failures stay inside that structured boundary. They do not expose
the returned path, original exception text, module target or traceback, and a
later valid duplicate entry point may still resolve successfully.

Plugin allowlists continue to contain reviewed raw entry-point names. Exact long
names are accepted; operators neither provide nor receive diagnostic digest
tokens.

Prefer a fresh environment containing one exact, mutually supported constraint
set. For this release line the RExecOp-owned core constraints are:

```bash
python -m venv .venv-fresh && source .venv-fresh/bin/activate
python -m pip install --upgrade pip
python -m pip install \
  "rexecop==1.0.0rc1" \
  "govengine==1.0.0rc1" \
  "sclite-core==2.0.0" \
  "<profile-or-plugin>==<exact-compatible-version>"
python -m pip check
rexecop doctor --profile <registered-profile>
```

When inspecting an existing environment instead, first repair or remove each
identified incompatible distribution. Then run `python -m pip check`, rerun
`rexecop doctor`, and do not continue to execution until both the dependency
graph and plugin posture pass. Doctor only reports the incompatibility; it does
not select, install, remove or upgrade packages. This guidance does not make
sequential `pip` replacement atomic and does not qualify any currently
incompatible Tecrax graph. Plugin imports and registrars remain trusted
in-process work without sandbox or timeout/hang containment; bounded diagnostic
serialization also does not contain a hostile or hanging `__str__` method.

## Coordinated editable install

The current `main` source line is qualified with exact public
`govengine==1.0.0rc2` and `sclite-core==2.0.1`. This does not rewrite the
dependency metadata of the already published `rexecop==1.0.0rc1` wheel.

```bash
git clone https://github.com/rozmiarD/RExecOP.git
cd RExecOP
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
git clone https://github.com/rozmiarD/GovEngine.git ../govengine
git -C ../govengine checkout e65ad22ec25d74bbbb4969bd614981a8ed5e47c8
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
python -m build --outdir /tmp/rexecop-dist
python -m twine check /tmp/rexecop-dist/*
python scripts/validate_distribution.py /tmp/rexecop-dist
python scripts/validate_supply_chain_gate.py /tmp/rexecop-dist \
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

Before dispatching publish, create `v<version>` at the exact green `main`
commit. The workflow resolves the protected tag, requires its commit to be an
ancestor of the dispatch commit, then checks out and builds that immutable
tagged source. Release evidence records the tag commit rather than the workflow
dispatch commit. After PyPI upload and public-index smoke it creates the
corresponding GitHub Release and attaches:

- `rexecop-release-evidence-<version>.json`;
- `rexecop-<version>.cdx.json`.

The workflow parses the package version using PEP 440 semantics and marks
alpha, beta, release-candidate, and development versions as GitHub
prereleases. Invalid versions fail before Release creation.

Wheel and sdist remain on PyPI. GitHub artifact attestations bind their digests
with the SBOM and evidence. A later train may name a previous evidence-backed
version; its record is then downloaded from `v<previous-version>` Release assets
and validated before upload. Leave that input empty only for the first
evidence-backed line.

The official publisher action is pinned to a reviewed full commit SHA. The
workflow carries no long-lived PyPI credential and rejects token-based upload
settings through `scripts/validate_workflow_security.py`. Only the staged wheel
and sdist directory is passed to PyPI; the CycloneDX SBOM remains outside the
upload directory and is retained for attestation and release evidence.

Do not store upload tokens in the repository, handoffs, or agent memory.

## Install an immutable Git revision

For the immutable public RExecOp `v1.0.0rc1` line, retain its original
GovEngine/SCLite dependency set:

```bash
python -m pip install \
  "govengine @ git+https://github.com/rozmiarD/GovEngine.git@v1.0.0rc1"
python -m pip install \
  "rexecop @ git+https://github.com/rozmiarD/RExecOP.git@v1.0.0rc1"
```

Use a reviewed tag or full commit SHA, not a moving branch. The selected source
must still satisfy the exact `govengine==1.0.0rc1` and
`sclite-core==2.0.0` metadata pins.

For current-source integration, use the reviewed RExecOp commit and let its
metadata resolve the exact `govengine==1.0.0rc2` / `sclite-core==2.0.1` pair;
do not combine that source with the old public-wheel dependency set.

## Private index / GitHub Packages (operator-owned)

Mirror the exact public wheel/sdist and pinned GovEngine/SCLite distributions
into a PyPI-compatible index. Preserve filenames and SHA-256 digests from
release evidence, then verify from a clean environment:

```bash
python -m pip install \
  --index-url https://packages.example.invalid/simple \
  "rexecop==1.0.0rc1"
python -m pip check
rexecop version
```

Index authentication, TLS trust and retention are operator-owned. Do not place
index credentials in repository configuration or command examples.

## Version and doc alignment

Before sharing an install artifact outside your host:

```bash
python scripts/validate_public_truth.py
pytest -q
```

See [Operator runbook](../OPERATOR_RUNBOOK.md) for the stable read-only path and
[Lab runbook](../OPERATOR_LAB_RUNBOOK.md) for public fixtures, mutation-block
checks and release qualification.

## Related

- [README.md](../README.md) — project overview
- [CHANGELOG.md](../CHANGELOG.md) — release history
- [known-limitations.md](known-limitations.md) — release-candidate non-claims
