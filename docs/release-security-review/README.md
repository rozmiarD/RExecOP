# Release security review records

Every release candidate requires an explicit security/process review record
before publication. Starting with the 1.0 release-candidate line, the record
must use `independent_review`; the historical
`solo_reviewed_alpha_risk` mode is accepted only for pre-1.0 alpha provenance.
The published `1.0.0rc1` v0.1 record is grandfathered. Every later release
record uses the source-bound v0.2 schema.

The gate is `scripts/validate_external_review_gate.py`.

## Release-candidate record

The published `1.0.0rc1` record uses the historical v0.1 schema:

```json
{
  "schema": "rexecop.release_security_review.v0.1",
  "version": "1.0.0rc1",
  "review_mode": "independent_review",
  "reviewed_at": "2026-07-25",
  "reviewer_ref": "reviewer:example",
  "surfaces": [
    "governance_admission_binding",
    "mutation_gates",
    "connector_output_safety",
    "release_train_scripts",
    "supply_chain_workflow"
  ],
  "notes": "Independent review completed for the described release-candidate delta."
}
```

This record proves the declared review event and surfaces. Because v0.1 does
not have a structured source field, prose notes alone must not be treated as a
machine-verifiable final-tag binding.

## Source-bound record

Every release after `1.0.0rc1`, including later release candidates and final
releases, requires v0.2. `reviewed_source_commit` identifies the immutable code
commit actually reviewed:

```json
{
  "schema": "rexecop.release_security_review.v0.2",
  "version": "1.0.0",
  "review_mode": "independent_review",
  "reviewed_at": "2026-08-01",
  "reviewer_ref": "reviewer:example",
  "reviewed_source_commit": "0123456789abcdef0123456789abcdef01234567",
  "surfaces": [
    "governance_admission_binding",
    "mutation_gates",
    "connector_output_safety",
    "release_train_scripts",
    "supply_chain_workflow"
  ],
  "notes": "Independent review completed for the exact release source."
}
```

The reviewer adds the record in a separate evidence-only commit after reviewing
the source commit. The protected release tag points to that evidence commit.
The publish workflow passes the protected tag commit to the gate:

```bash
python scripts/validate_external_review_gate.py \
  --version <version> \
  --release-commit <40-character-tag-commit>
```

The gate requires the reviewed source to be an ancestor of the release commit
and permits no intervening file change except
`docs/release-security-review/<version>.json`. This avoids an impossible
self-referential commit hash while ensuring that unreviewed code cannot enter
the tag. An invalid version, missing independent review, invalid source commit,
unreviewed delta or incomplete review surface fails closed.

Review records are evidence authored through the review process. Maintainers
must not silently rewrite reviewer identity, scope or conclusions after
approval; corrections require a new reviewed change.
