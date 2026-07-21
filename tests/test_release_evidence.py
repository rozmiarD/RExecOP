from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_evidence.py"


def _load():
    spec = importlib.util.spec_from_file_location("rexecop_release_evidence", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_distribution_digests_require_wheel_and_sdist(tmp_path: Path) -> None:
    evidence = _load()
    (tmp_path / "rexecop-1.0.0-py3-none-any.whl").write_bytes(b"wheel")
    try:
        evidence.distribution_digests(tmp_path)
    except ValueError as exc:
        assert str(exc) == "release_evidence_missing_sdist"
    else:
        raise AssertionError("missing sdist must fail closed")


def test_public_artifact_identity_mismatch_is_rejected() -> None:
    evidence = _load()
    record = {
        "schema": evidence.SCHEMA,
        "status": "passed",
        "version": "1.0.0",
        "source_commit": "a" * 40,
        "workflow_run_id": "123",
        "workflow_run_url": "https://github.com/rozmiarD/RExecOP/actions/runs/123",
        "artifacts": {
            "rexecop-1.0.0-py3-none-any.whl": "b" * 64,
            "rexecop-1.0.0.tar.gz": "c" * 64,
        },
        "public_artifacts": {
            "rexecop-1.0.0-py3-none-any.whl": "d" * 64,
            "rexecop-1.0.0.tar.gz": "c" * 64,
        },
        "installed_versions": {
            "rexecop": "1.0.0",
            "govengine": "1.0.0",
            "sclite-core": "2.0.0",
            "tecrax": "1.0.0",
        },
        "doctor_status": "passed",
    }
    sealed = evidence.seal_record(record)
    assert "release_evidence_public_artifact_identity_mismatch" in evidence.validate_record(
        sealed,
        expected_version="1.0.0",
    )


def _v2_record(evidence) -> dict:
    artifacts = {
        "rexecop-1.0.0-py3-none-any.whl": "b" * 64,
        "rexecop-1.0.0.tar.gz": "c" * 64,
    }
    sbom = {
        "filename": "rexecop-1.0.0.cdx.json",
        "sha256": "d" * 64,
        "format": "CycloneDX",
        "spec_version": "1.6",
    }
    return {
        "schema": evidence.SCHEMA,
        "status": "passed",
        "version": "1.0.0",
        "source_commit": "a" * 40,
        "workflow_run_id": "123",
        "workflow_run_url": "https://github.com/rozmiarD/RExecOP/actions/runs/123",
        "artifacts": artifacts,
        "public_artifacts": dict(artifacts),
        "sbom": sbom,
        "attestation": {
            "id": "456",
            "url": "https://github.com/rozmiarD/RExecOP/attestations/456",
            "predicate_type": evidence.ATTESTATION_PREDICATE_TYPE,
            "subjects": {**artifacts, sbom["filename"]: sbom["sha256"]},
        },
        "installed_versions": {
            "rexecop": "1.0.0",
            "govengine": "1.0.0",
            "sclite-core": "2.0.0",
            "tecrax": "1.0.0",
        },
        "doctor_status": "passed",
    }


def test_v2_record_requires_sbom() -> None:
    evidence = _load()
    record = _v2_record(evidence)
    del record["sbom"]

    errors = evidence.validate_record(evidence.seal_record(record), expected_version="1.0.0")

    assert "release_evidence_missing_sbom" in errors


def test_v2_record_rejects_attested_subject_drift() -> None:
    evidence = _load()
    record = _v2_record(evidence)
    record["attestation"]["subjects"]["rexecop-1.0.0.cdx.json"] = "e" * 64

    errors = evidence.validate_record(evidence.seal_record(record), expected_version="1.0.0")

    assert "release_evidence_attested_subject_identity_mismatch" in errors


def test_sbom_descriptor_rejects_non_cyclonedx(tmp_path: Path) -> None:
    evidence = _load()
    path = tmp_path / "rexecop-1.0.0.cdx.json"
    path.write_text('{"bomFormat":"SPDX"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="release_evidence_invalid_sbom_format"):
        evidence.sbom_descriptor(path)
