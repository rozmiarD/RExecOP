from __future__ import annotations

import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_public_index_release_smoke.py"
CLEAN_INSTALL = ROOT / "scripts" / "validate_clean_install_smoke.py"
PREFLIGHT = ROOT / "scripts" / "validate_release_train_preflight.py"


def _write_sbom(dist: Path, version: str) -> Path:
    path = dist / f"rexecop-{version}.cdx.json"
    path.write_text(
        json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6"}) + "\n",
        encoding="utf-8",
    )
    return path


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_index_release_smoke_orchestration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _load("rexecop_public_index_release_smoke", SCRIPT)
    clean_install = _load("rexecop_clean_install_smoke", CLEAN_INSTALL)

    class FakeCompleted:
        def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    venv = tmp_path / "venv"
    venv.mkdir()
    venv_python = venv / "bin" / "python"
    rexecop_bin = venv / "bin" / "rexecop"
    version = "0.2.24a0"
    marker = clean_install.clean_install_marker(version)
    runtime_root = venv / "runtime"

    def fake_run(command: list[str], *, cwd: Path | None = None) -> FakeCompleted:
        if command[:2] == [str(rexecop_bin), "version"]:
            return FakeCompleted(stdout=f"{version}\n")
        if command[:4] == [str(rexecop_bin), "--root", str(runtime_root), "init"]:
            return FakeCompleted(stdout="ok\n")
        if command[:5] == [str(rexecop_bin), "--json", "--root", str(runtime_root), "doctor"]:
            return FakeCompleted(stdout=json.dumps({"status": "passed", "blockers": []}) + "\n")
        if command[:2] == [str(venv_python), "-c"]:
            return FakeCompleted(
                stdout=json.dumps(
                    {
                        "rexecop": version,
                        "govengine": "0.16.11",
                        "sclite-core": "1.0.9",
                        "tecrax": "0.3.21a0",
                    }
                )
                + "\n"
            )
        raise AssertionError(f"unexpected_command:{command}")

    @contextmanager
    def fake_install(*_args, **_kwargs):
        yield venv, venv_python, rexecop_bin

    monkeypatch.setattr(clean_install, "isolated_pypi_install", fake_install)
    monkeypatch.setattr(clean_install, "run_surface_smoke", lambda *_a, **_k: marker)
    monkeypatch.setattr(clean_install, "_run", fake_run)

    def fake_load(name: str, path: Path):
        if path == CLEAN_INSTALL:
            return clean_install
        return _load(name, path)

    monkeypatch.setattr(release, "_load_module", fake_load)
    details = release.run_public_index_checks(version, tmp_parent=tmp_path)

    assert details["surface_marker"] == marker
    assert details["version"] == version
    assert details["doctor_status"] == "passed"
    assert details["installed_versions"]["rexecop"] == version


def test_public_index_release_smoke_writes_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _load("rexecop_public_index_release_smoke", SCRIPT)
    clean_install = _load("rexecop_clean_install_smoke_current", CLEAN_INSTALL)
    version = clean_install.project_version()
    evidence_dir = tmp_path / "release-evidence"
    monkeypatch.setattr(release, "RELEASE_EVIDENCE_DIR", evidence_dir)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / f"rexecop-{version}-py3-none-any.whl").write_bytes(b"wheel")
    (dist / f"rexecop-{version}.tar.gz").write_bytes(b"sdist")
    sbom = _write_sbom(dist, version)
    public_artifacts = release.distribution_digests(dist)

    path = release.write_release_evidence(
        version,
        {
            "surface_marker": f"clean_install_smoke_ok:rexecop=={version}",
            "version": version,
            "doctor_status": "passed",
            "installed_versions": {
                "rexecop": version,
                "govengine": "1.0.0rc1",
                "sclite-core": "2.0.0",
                "tecrax": "0.4.0rc3",
            },
        },
        dist_dir=dist,
        output=evidence_dir / f"{version}.json",
        source_commit="a" * 40,
        workflow_run_id="123",
        workflow_run_url="https://github.com/rozmiarD/RExecOP/actions/runs/123",
        sbom_path=sbom,
        attestation_id="456",
        attestation_url="https://github.com/rozmiarD/RExecOP/attestations/456",
        public_artifacts=public_artifacts,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == "rexecop.release_evidence.v2"
    assert payload["sbom"]["sha256"]
    assert payload["attestation"]["subjects"][sbom.name] == payload["sbom"]["sha256"]
    assert payload["installed_versions"]["rexecop"] == version
    assert len(payload["record_digest"]) == 64


def test_public_index_release_smoke_cli_success(monkeypatch: pytest.MonkeyPatch) -> None:
    release = _load("rexecop_public_index_release_smoke", SCRIPT)
    version = "0.2.24a0"
    monkeypatch.setattr(
        release,
        "run_public_index_checks",
        lambda *_args, **_kwargs: {
            "surface_marker": f"clean_install_smoke_ok:rexecop=={version}",
            "version": version,
            "doctor_status": "passed",
            "installed_versions": {},
        },
    )
    assert release.main(["--version", version]) == 0


def test_public_index_release_smoke_verify_post_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _load("rexecop_public_index_release_smoke", SCRIPT)
    clean_install = _load("rexecop_clean_install_smoke_post_publish", CLEAN_INSTALL)
    version = clean_install.project_version()
    evidence_dir = tmp_path / "release-evidence"
    monkeypatch.setattr(release, "RELEASE_EVIDENCE_DIR", evidence_dir)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / f"rexecop-{version}-py3-none-any.whl").write_bytes(b"wheel")
    (dist / f"rexecop-{version}.tar.gz").write_bytes(b"sdist")
    sbom = _write_sbom(dist, version)
    monkeypatch.setattr(
        release,
        "pypi_release_digests",
        lambda *_args: release.distribution_digests(dist),
    )

    def fake_load(name: str, path: Path):
        module = _load(name, path)
        if path == PREFLIGHT:
            module.RELEASE_EVIDENCE_DIR = evidence_dir
            module._collect_sibling_repo_errors = lambda *_args, **_kwargs: None
        return module

    monkeypatch.setattr(release, "_load_module", fake_load)
    monkeypatch.setattr(
        release,
        "run_public_index_checks",
        lambda *_args, **_kwargs: {
            "surface_marker": f"clean_install_smoke_ok:rexecop=={version}",
            "version": version,
            "doctor_status": "passed",
            "installed_versions": {
                "rexecop": version,
                "govengine": "1.0.0rc1",
                "sclite-core": "2.0.0",
                "tecrax": "0.4.0rc3",
            },
        },
    )

    assert (
        release.main(
            [
                "--version",
                version,
                "--write-evidence",
                "--verify-post-publish",
                "--dist-dir",
                str(dist),
                "--sbom",
                str(sbom),
                "--attestation-id",
                "456",
                "--attestation-url",
                "https://github.com/rozmiarD/RExecOP/attestations/456",
                "--source-commit",
                "a" * 40,
                "--workflow-run-id",
                "123",
                "--workflow-run-url",
                "https://github.com/rozmiarD/RExecOP/actions/runs/123",
            ],
        )
        == 0
    )
    assert (evidence_dir / f"{version}.json").is_file()
    verified = fake_load("rexecop_validate_release_train_preflight", PREFLIGHT)
    assert verified.collect_errors(post_publish=True, stack_repos={}) == []
