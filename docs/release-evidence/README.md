# Release evidence

Release evidence for new publications is a versioned
`rexecop.release_evidence.v2` JSON record. It binds the published version to the
exact source commit, workflow run, wheel/sdist SHA-256 digests, CycloneDX SBOM,
GitHub artifact-attestation identity, installed GovEngine/SCLite/RExecOp/Tecrax
versions and doctor result. The record carries its own canonical
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

`publish.yml` first generates one provenance attestation whose subjects are the
exact wheel, sdist and SBOM. The v2 record must contain the same filenames and
SHA-256 digests plus that attestation's GitHub ID/URL. The workflow then attests
the record itself and persists both record and SBOM on the dedicated
`release-evidence` Git branch. Before another
upload, release-mode preflight downloads and validates the preceding supported
line's record from that durable ref:

```bash
python scripts/validate_release_train_preflight.py \
  --release \
  --previous-evidence .release-train/rexecop-release-evidence-<previous>.json
```

Missing evidence, a mismatched version, altered record digest, absent wheel/sdist
or SBOM, subject-digest drift, malformed attestation identity, non-green doctor
status or incomplete installed-version inventory fails closed.
`.github/workflows/repair-release-evidence.yml` is the bounded manual recovery path
for an already-published line; it reruns the public-index smoke, downloads the exact
public wheel and sdist, and publishes a replacement evidence record. A replacement
may explicitly name the prior line in `supersedes`.

Verify the persisted provenance subjects after downloading them:

```bash
gh attestation verify rexecop-<version>-py3-none-any.whl --repo rozmiarD/RExecOP
gh attestation verify rexecop-<version>.tar.gz --repo rozmiarD/RExecOP
gh attestation verify rexecop-<version>.cdx.json --repo rozmiarD/RExecOP
```

Verification must resolve to the RExecOP repository and the release workflow
recorded in evidence. The SBOM is an attested release artifact; it is not
uploaded to PyPI as a Python distribution.
