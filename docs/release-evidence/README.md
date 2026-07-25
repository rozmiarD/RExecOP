# Release evidence

Release evidence for new publications is a versioned
`rexecop.release_evidence.v2` JSON record. It binds the published version to the
exact source commit, workflow run, wheel/sdist SHA-256 digests, CycloneDX SBOM,
GitHub artifact-attestation identity, installed GovEngine/SCLite/RExecOp
versions and doctor result. Tecrax is a separate downstream consumer and is not
part of this release-evidence inventory. The record carries its own canonical
`record_digest`. Historical `rexecop.release_evidence.v1` records remain valid
only as previous-line evidence; a new post-publish record must use v2.

Preferred path:

```bash
python scripts/validate_public_index_release_smoke.py \
  --version <version> \
  --dist-dir dist \
  --sbom dist/rexecop-<version>.cdx.json \
  --attestation-id <github-attestation-id> \
  --attestation-url https://github.com/rozmiarD/RExecOP/attestations/<id> \
  --evidence-output .release-train/rexecop-release-evidence-<version>.json \
  --write-evidence \
  --verify-post-publish
```

`publish.yml` first resolves the protected `v<version>` tag, requires its commit
to be an ancestor of the workflow dispatch commit, and checks out that immutable
tagged source before validation and build. It then generates one provenance
attestation whose subjects are the exact wheel, sdist and SBOM. The v2 record
binds the tag commit and must contain the same filenames and SHA-256 digests plus
that attestation's GitHub ID/URL. The workflow attests the record itself and
publishes the record and SBOM as GitHub Release assets attached to that tag.

For the first evidence-backed line there is no artificial dependency on an
older alpha record. A later train can explicitly name its preceding
evidence-backed version; publish downloads
`rexecop-release-evidence-<previous>.json` from the corresponding
`v<previous>` GitHub Release before running:

```bash
python scripts/validate_release_train_preflight.py \
  --release \
  --previous-evidence .release-train/rexecop-release-evidence-<previous>.json
```

When a previous version is declared, missing evidence, a mismatched version,
altered record digest, absent wheel/sdist
or SBOM, subject-digest drift, malformed attestation identity, non-green doctor
status or incomplete installed-version inventory fails closed.
`.github/workflows/repair-release-evidence.yml` is the bounded manual recovery path
for an already-published line; it reruns the public-index smoke, downloads the exact
public wheel and sdist, verifies that the version tag still resolves to the
declared source commit, and replaces the Release evidence/SBOM assets only after
fresh attestations. A replacement may explicitly name the prior line in
`supersedes`.

Verify the persisted provenance subjects after downloading them:

```bash
gh attestation verify rexecop-<version>-py3-none-any.whl --repo rozmiarD/RExecOP
gh attestation verify rexecop-<version>.tar.gz --repo rozmiarD/RExecOP
gh attestation verify rexecop-<version>.cdx.json --repo rozmiarD/RExecOP
```

Verification must resolve to the RExecOP repository and the release workflow
recorded in evidence. The SBOM is an attested release artifact; it is not
uploaded to PyPI as a Python distribution.
