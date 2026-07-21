from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

LEGACY_SCHEMA = "rexecop.release_evidence.v1"
SCHEMA = "rexecop.release_evidence.v2"
REQUIRED_PACKAGES = ("rexecop", "govengine", "sclite-core", "tecrax")
ATTESTATION_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ATTESTATION_ID = re.compile(r"^[1-9][0-9]*$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def distribution_digests(dist_dir: Path) -> dict[str, str]:
    files = sorted((*dist_dir.glob("*.whl"), *dist_dir.glob("*.tar.gz")))
    if not any(path.suffix == ".whl" for path in files):
        raise ValueError("release_evidence_missing_wheel")
    if not any(path.name.endswith(".tar.gz") for path in files):
        raise ValueError("release_evidence_missing_sdist")
    return {path.name: sha256_file(path) for path in files}


def sbom_descriptor(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError("release_evidence_missing_sbom")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("release_evidence_invalid_sbom") from exc
    if not isinstance(payload, dict) or payload.get("bomFormat") != "CycloneDX":
        raise ValueError("release_evidence_invalid_sbom_format")
    return {
        "filename": path.name,
        "sha256": sha256_file(path),
        "format": "CycloneDX",
        "spec_version": str(payload.get("specVersion") or ""),
    }


def pypi_release_digests(package: str, version: str) -> dict[str, str]:
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
        payload = json.load(response)
    urls = payload.get("urls") if isinstance(payload, dict) else None
    if not isinstance(urls, list):
        raise ValueError("release_evidence_invalid_pypi_response")
    result: dict[str, str] = {}
    for item in urls:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "")
        digests = item.get("digests")
        sha256 = str(digests.get("sha256") or "") if isinstance(digests, dict) else ""
        if filename and _SHA256.fullmatch(sha256):
            result[filename] = sha256
    if not result:
        raise ValueError("release_evidence_missing_pypi_artifacts")
    return result


def canonical_payload(record: Mapping[str, Any]) -> bytes:
    payload = {key: value for key, value in record.items() if key != "record_digest"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def record_digest(record: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(record)).hexdigest()


def seal_record(record: Mapping[str, Any]) -> dict[str, Any]:
    sealed = dict(record)
    sealed["record_digest"] = record_digest(sealed)
    return sealed


def validate_record(
    record: Mapping[str, Any],
    *,
    expected_version: str | None = None,
) -> list[str]:
    errors: list[str] = []
    schema = str(record.get("schema") or "")
    if schema not in {LEGACY_SCHEMA, SCHEMA}:
        errors.append(f"release_evidence_schema_mismatch:{schema}!={SCHEMA}")
    version = str(record.get("version") or "")
    if expected_version is not None and version != expected_version:
        errors.append(f"release_evidence_version_mismatch:{version}!={expected_version}")
    if record.get("status") != "passed":
        errors.append(f"release_evidence_status_not_passed:{record.get('status')}")
    source_commit = str(record.get("source_commit") or "")
    if not _COMMIT.fullmatch(source_commit):
        errors.append("release_evidence_invalid_source_commit")
    if not str(record.get("workflow_run_id") or ""):
        errors.append("release_evidence_missing_workflow_run_id")
    if not str(record.get("workflow_run_url") or "").startswith("https://github.com/"):
        errors.append("release_evidence_invalid_workflow_run_url")

    artifacts = record.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        errors.append("release_evidence_missing_artifacts")
    else:
        names = [str(name) for name in artifacts]
        if not any(name.endswith(".whl") for name in names):
            errors.append("release_evidence_missing_wheel")
        if not any(name.endswith(".tar.gz") for name in names):
            errors.append("release_evidence_missing_sdist")
        for name, digest in artifacts.items():
            if not _SHA256.fullmatch(str(digest)):
                errors.append(f"release_evidence_invalid_artifact_digest:{name}")
    public_artifacts = record.get("public_artifacts")
    if not isinstance(public_artifacts, dict) or public_artifacts != artifacts:
        errors.append("release_evidence_public_artifact_identity_mismatch")

    if schema == SCHEMA:
        _validate_v2_supply_chain(record, artifacts, errors)

    installed = record.get("installed_versions")
    if not isinstance(installed, dict):
        errors.append("release_evidence_missing_installed_versions")
    else:
        for package in REQUIRED_PACKAGES:
            if not str(installed.get(package) or ""):
                errors.append(f"release_evidence_missing_installed_version:{package}")
    if isinstance(installed, dict) and str(installed.get("rexecop") or "") != version:
        errors.append("release_evidence_installed_rexecop_mismatch")
    if record.get("doctor_status") not in {"passed", "warning"}:
        errors.append(f"release_evidence_doctor_not_green:{record.get('doctor_status')}")
    actual_digest = str(record.get("record_digest") or "")
    expected_digest = record_digest(record)
    if actual_digest != expected_digest:
        errors.append(f"release_evidence_digest_mismatch:{actual_digest}!={expected_digest}")
    return errors


def _validate_v2_supply_chain(
    record: Mapping[str, Any],
    artifacts: object,
    errors: list[str],
) -> None:
    sbom = record.get("sbom")
    if not isinstance(sbom, dict):
        errors.append("release_evidence_missing_sbom")
        sbom_subjects: dict[str, str] = {}
    else:
        filename = str(sbom.get("filename") or "")
        digest = str(sbom.get("sha256") or "")
        if not filename.endswith(".cdx.json"):
            errors.append("release_evidence_invalid_sbom_filename")
        if not _SHA256.fullmatch(digest):
            errors.append("release_evidence_invalid_sbom_digest")
        if sbom.get("format") != "CycloneDX":
            errors.append("release_evidence_invalid_sbom_format")
        if not str(sbom.get("spec_version") or ""):
            errors.append("release_evidence_missing_sbom_spec_version")
        sbom_subjects = {filename: digest} if filename and _SHA256.fullmatch(digest) else {}

    attestation = record.get("attestation")
    if not isinstance(attestation, dict):
        errors.append("release_evidence_missing_attestation")
        return
    attestation_id = str(attestation.get("id") or "")
    attestation_url = str(attestation.get("url") or "")
    if not _ATTESTATION_ID.fullmatch(attestation_id):
        errors.append("release_evidence_invalid_attestation_id")
    expected_url = f"https://github.com/rozmiarD/RExecOP/attestations/{attestation_id}"
    if attestation_url != expected_url:
        errors.append("release_evidence_invalid_attestation_url")
    if attestation.get("predicate_type") != ATTESTATION_PREDICATE_TYPE:
        errors.append("release_evidence_invalid_attestation_predicate")
    expected_subjects = dict(artifacts) if isinstance(artifacts, dict) else {}
    expected_subjects.update(sbom_subjects)
    if attestation.get("subjects") != expected_subjects:
        errors.append("release_evidence_attested_subject_identity_mismatch")


def load_record(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("release_evidence_not_object")
    return payload


def write_record(path: Path, record: Mapping[str, Any]) -> Path:
    sealed = seal_record(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sealed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
