from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_release_train_preflight.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "rexecop_validate_release_train_preflight",
        SCRIPT,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_evidence(
    path: Path,
    version: str,
    *,
    supersedes: str = "",
    legacy: bool = False,
) -> Path:
    validator = _load_validator()
    release_evidence = validator._load_module(
        "rexecop_release_evidence_test",
        ROOT / "scripts" / "release_evidence.py",
    )
    artifacts = {
        f"rexecop-{version}-py3-none-any.whl": "b" * 64,
        f"rexecop-{version}.tar.gz": "c" * 64,
    }
    sbom = {
        "filename": f"rexecop-{version}.cdx.json",
        "sha256": "d" * 64,
        "format": "CycloneDX",
        "spec_version": "1.6",
    }
    record = {
        "schema": release_evidence.LEGACY_SCHEMA if legacy else release_evidence.SCHEMA,
        "status": "passed",
        "version": version,
        "recorded_at": "2026-07-12T00:00:00+00:00",
        "source_commit": "a" * 40,
        "workflow_run_id": "123",
        "workflow_run_url": "https://github.com/rozmiarD/RExecOP/actions/runs/123",
        "artifacts": artifacts,
        "public_artifacts": dict(artifacts),
        "installed_versions": {
            "rexecop": version,
            "govengine": "0.16.11",
            "sclite-core": "1.0.9",
            "tecrax": "0.3.21a0",
        },
        "doctor_status": "passed",
        "surface_marker": f"clean_install_smoke_ok:rexecop=={version}",
        "supersedes": supersedes,
    }
    if not legacy:
        record["sbom"] = sbom
        record["attestation"] = {
            "id": "456",
            "url": "https://github.com/rozmiarD/RExecOP/attestations/456",
            "predicate_type": release_evidence.ATTESTATION_PREDICATE_TYPE,
            "subjects": {**artifacts, sbom["filename"]: sbom["sha256"]},
        }
    record["record_digest"] = release_evidence.record_digest(record)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_release_train_preflight_passes(tmp_path: Path) -> None:
    validator = _load_validator()
    public_truth, _ = validator._validator_modules()
    version = public_truth.current_version()
    projects = {
        "govengine": f'''
[project]
name = "govengine"
version = "1.0.0rc1"
dependencies = ["{public_truth.EXPECTED_SCLITE}"]
''',
        "sclite": '''
[project]
name = "sclite-core"
version = "2.0.0"
dependencies = []
''',
        "tecrax": f'''
[project]
name = "tecrax"
version = "0.4.0rc3"
dependencies = [
  "{public_truth.EXPECTED_GOVENGINE}",
  "{public_truth.EXPECTED_SCLITE}",
  "rexecop=={version}",
]
''',
    }
    env = os.environ.copy()
    for name, content in projects.items():
        repo = tmp_path / name
        repo.mkdir()
        (repo / "pyproject.toml").write_text(content, encoding="utf-8")
        env[validator._STACK_REPO_ENV[name]] = str(repo)
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert result.stdout.strip() == validator.success_line(
        version,
        post_publish=False,
        govengine=public_truth.EXPECTED_GOVENGINE,
        sclite=public_truth.EXPECTED_SCLITE,
        tecrax=public_truth.EXPECTED_TECRAX_EXTRA,
    )


def test_release_train_preflight_rejects_validator_constant_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    public_truth, stack_contracts = validator._validator_modules()
    monkeypatch.setattr(stack_contracts, "EXPECTED_GOVENGINE", "govengine==9.9.9")
    errors = validator.collect_errors(stack_repos={})
    assert any(
        error.startswith("validator_constant_mismatch:EXPECTED_GOVENGINE:")
        for error in errors
    )


def test_release_train_preflight_rejects_tecrax_pin_drift(tmp_path: Path) -> None:
    validator = _load_validator()
    public_truth, _ = validator._validator_modules()
    tecrax_root = tmp_path / "tecrax"
    tecrax_root.mkdir()
    (tecrax_root / "pyproject.toml").write_text(
        """
[project]
name = "tecrax"
version = "0.3.21a0"
dependencies = [
  "govengine==0.16.11",
  "sclite-core==1.0.9",
  "rexecop==9.9.9a0",
]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    errors = validator.collect_errors(stack_repos={"tecrax": tecrax_root})
    assert any("tecrax_repo_rexecop_pin_mismatch" in item for item in errors)


def test_release_train_preflight_rejects_sclite_repo_version_drift(tmp_path: Path) -> None:
    validator = _load_validator()
    sclite_root = tmp_path / "sclite"
    sclite_root.mkdir()
    (sclite_root / "pyproject.toml").write_text(
        '''
[project]
name = "sclite-core"
version = "2.0.0rc1"
dependencies = []
'''.strip()
        + "\n",
        encoding="utf-8",
    )
    errors = validator.collect_errors(stack_repos={"sclite": sclite_root})
    assert "sclite_repo_version_mismatch:2.0.0rc1!=2.0.0" in errors


def test_release_train_preflight_rejects_missing_sibling_repos() -> None:
    validator = _load_validator()
    errors = validator.collect_errors(stack_repos={})
    assert "sibling_repo_missing:govengine" in errors
    assert "sibling_repo_missing:sclite" in errors
    assert "sibling_repo_missing:tecrax" in errors


def test_release_train_preflight_post_publish_requires_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    monkeypatch.setattr(validator, "RELEASE_EVIDENCE_DIR", tmp_path)
    errors = validator.collect_errors(post_publish=True, stack_repos={})
    assert any(error.startswith("release_evidence_missing:") for error in errors)


def test_release_train_preflight_post_publish_accepts_valid_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    version = validator._validator_modules()[0].current_version()
    monkeypatch.setattr(validator, "RELEASE_EVIDENCE_DIR", tmp_path)
    _write_evidence(tmp_path / f"{version}.json", version)
    errors = validator.collect_errors(post_publish=True, stack_repos={})
    assert not any(error.startswith("release_evidence_") for error in errors)


def test_release_train_preflight_post_publish_rejects_legacy_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    version = validator._validator_modules()[0].current_version()
    monkeypatch.setattr(validator, "RELEASE_EVIDENCE_DIR", tmp_path)
    _write_evidence(tmp_path / f"{version}.json", version, legacy=True)

    errors = validator.collect_errors(post_publish=True, stack_repos={})

    assert any(error.startswith("release_evidence_current_schema_required:") for error in errors)


def test_release_preflight_rejects_missing_previous_evidence() -> None:
    validator = _load_validator()
    errors = validator.collect_errors(release_mode=True, stack_repos={})
    assert any(error.startswith("previous_release_evidence_not_configured:") for error in errors)


def test_release_preflight_rejects_digest_mismatch(tmp_path: Path) -> None:
    validator = _load_validator()
    version = validator._validator_modules()[0].PUBLISHED_PYPI_VERSION
    path = _write_evidence(tmp_path / "evidence.json", version)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["doctor_status"] = "blocker"
    path.write_text(json.dumps(record), encoding="utf-8")
    errors = validator.collect_errors(
        release_mode=True,
        previous_evidence=path,
        stack_repos={},
    )
    assert any(error.startswith("release_evidence_digest_mismatch:") for error in errors)


def test_release_preflight_accepts_superseded_repair(tmp_path: Path) -> None:
    validator = _load_validator()
    previous = validator._validator_modules()[0].PUBLISHED_PYPI_VERSION
    path = _write_evidence(tmp_path / "repair.json", "0.2.25a0", supersedes=previous)
    errors = validator.collect_errors(
        release_mode=True,
        previous_evidence=path,
        stack_repos={},
    )
    assert not any(error.startswith("release_evidence_") for error in errors)


def test_release_preflight_accepts_legacy_previous_line_evidence(tmp_path: Path) -> None:
    validator = _load_validator()
    previous = validator._validator_modules()[0].PUBLISHED_PYPI_VERSION
    path = _write_evidence(tmp_path / "legacy.json", previous, legacy=True)

    errors = validator.collect_errors(
        release_mode=True,
        previous_evidence=path,
        stack_repos={},
    )

    assert not any(error.startswith("release_evidence_") for error in errors)
