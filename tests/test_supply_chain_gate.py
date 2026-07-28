from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_supply_chain_gate.py"
ARTIFACT_SCRIPT = ROOT / "scripts" / "validate_artifact_install_smoke.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("rexecop_validate_supply_chain_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_artifact() -> Any:
    spec = importlib.util.spec_from_file_location(
        "rexecop_validate_artifact_install_smoke", ARTIFACT_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_wheel(
    directory: Path,
    name: str,
    version: str,
    *,
    metadata_name: str | None = None,
    metadata_version: str | None = None,
    payload: str = "value = 1\n",
    requires_dist: str | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    escaped_name = name.replace("-", "_").replace(".", "_")
    dist_info = f"{escaped_name}-{version}.dist-info"
    wheel = directory / f"{escaped_name}-{version}-py3-none-any.whl"
    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: {metadata_name or name}\n"
        f"Version: {metadata_version or version}\n"
    )
    if requires_dist is not None:
        metadata += f"Requires-Dist: {requires_dist}\n"
    metadata += "\n"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{escaped_name}/__init__.py", payload)
        archive.writestr(f"{dist_info}/METADATA", metadata)
        archive.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: rexecop-test\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{dist_info}/RECORD", "")
    return wheel


def _run_provenance(
    artifact: Any,
    venv_python: Path,
    candidate: Any,
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        artifact._candidate_provenance_command(venv_python, [candidate]),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def test_candidate_install_uses_deterministic_hashed_constraints(tmp_path: Path) -> None:
    artifact = _load_artifact()
    zed_dir = tmp_path / "zed-wheels"
    alpha_dir = tmp_path / "alpha-wheels"
    zed = _write_wheel(zed_dir, "Zed_Pkg", "2.0")
    alpha = _write_wheel(alpha_dir, "Alpha.Pkg", "1.0")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    options, candidates = artifact._candidate_install([zed_dir, alpha_dir], workspace)

    constraint = workspace / "candidate-constraints.txt"
    alpha_digest = hashlib.sha256(alpha.read_bytes()).hexdigest()
    zed_digest = hashlib.sha256(zed.read_bytes()).hexdigest()
    assert options == ["--constraint", str(constraint)]
    assert [candidate.normalized_name for candidate in candidates] == ["alpha-pkg", "zed-pkg"]
    assert constraint.read_text(encoding="utf-8").splitlines() == [
        f"alpha-pkg @ {alpha.resolve().as_uri()}#sha256={alpha_digest}",
        f"zed-pkg @ {zed.resolve().as_uri()}#sha256={zed_digest}",
    ]
    assert "--find-links" not in options


def test_candidate_install_without_directories_preserves_public_resolution(
    tmp_path: Path,
) -> None:
    artifact = _load_artifact()

    assert artifact._candidate_install([], tmp_path) == ([], ())


def test_candidate_install_rejects_missing_or_empty_wheelhouse(tmp_path: Path) -> None:
    artifact = _load_artifact()

    with pytest.raises(RuntimeError, match="candidate_wheel_dir_missing"):
        artifact._candidate_install([tmp_path / "missing"], tmp_path)

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RuntimeError, match="candidate_wheel_dir_empty"):
        artifact._candidate_install([empty], tmp_path)


@pytest.mark.parametrize(
    ("fixture", "error"),
    [
        ("corrupt", "candidate_wheel_invalid_zip"),
        ("invalid_name", "candidate_wheel_metadata_invalid"),
        ("identity_mismatch", "candidate_wheel_identity_mismatch"),
        ("filename_mismatch", "candidate_wheel_filename_mismatch"),
    ],
)
def test_candidate_install_rejects_invalid_wheels(
    tmp_path: Path,
    fixture: str,
    error: str,
) -> None:
    artifact = _load_artifact()
    wheelhouse = tmp_path / fixture
    wheelhouse.mkdir()
    if fixture == "corrupt":
        (wheelhouse / "demo-1.0-py3-none-any.whl").write_bytes(b"not-a-wheel")
    elif fixture == "invalid_name":
        _write_wheel(wheelhouse, "demo", "1.0", metadata_name="not a valid name!")
    elif fixture == "filename_mismatch":
        wheel = _write_wheel(wheelhouse, "demo", "1.0")
        wheel.rename(wheelhouse / "other-1.0-py3-none-any.whl")
    else:
        _write_wheel(wheelhouse, "demo", "1.0", metadata_name="other")

    with pytest.raises(RuntimeError, match=error):
        artifact._candidate_install([wheelhouse], tmp_path)


def test_candidate_install_rejects_normalized_duplicates_and_rexecop(tmp_path: Path) -> None:
    artifact = _load_artifact()
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_wheel(first, "Demo_Pkg", "1.0")
    _write_wheel(second, "demo-pkg", "1.0")

    with pytest.raises(RuntimeError, match="candidate_wheel_duplicate_name:demo-pkg"):
        artifact._candidate_install([first, second], tmp_path)

    rexecop = tmp_path / "rexecop"
    _write_wheel(rexecop, "RExecOp", "1.0")
    with pytest.raises(RuntimeError, match="candidate_wheel_rexecop_forbidden"):
        artifact._candidate_install([rexecop], tmp_path)


def test_candidate_provenance_rejects_source_wheel_hash_change(tmp_path: Path) -> None:
    artifact = _load_artifact()
    wheelhouse = tmp_path / "wheels"
    wheel = _write_wheel(wheelhouse, "demo", "1.0")
    _options, candidates = artifact._candidate_install([wheelhouse], tmp_path)
    with wheel.open("ab") as handle:
        handle.write(b"changed")

    with pytest.raises(RuntimeError, match="candidate_wheel_digest_changed:demo"):
        artifact._candidate_provenance_command(sys.executable, candidates)


@pytest.mark.skipif(os.name != "posix", reason="venv artifact smoke requires POSIX")
def test_candidate_provenance_accepts_exact_pep610_and_rejects_missing_or_wrong(
    tmp_path: Path,
) -> None:
    artifact = _load_artifact()
    wheelhouse = tmp_path / "wheels"
    _write_wheel(wheelhouse, "demo-pkg", "1.0")
    options, candidates = artifact._candidate_install([wheelhouse], tmp_path)
    candidate = candidates[0]
    root_wheel = _write_wheel(
        tmp_path / "root-wheel",
        "root-pkg",
        "1.0",
        requires_dist="demo-pkg==1.0",
    )
    venv = tmp_path / "venv"
    create = subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert create.returncode == 0, create.stderr
    venv_python = artifact._python(venv)
    install = subprocess.run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "-q",
            "--disable-pip-version-check",
            *options,
            str(root_wheel),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert install.returncode == 0, install.stderr

    accepted = _run_provenance(artifact, venv_python, candidate, cwd=tmp_path)
    assert accepted.returncode == 0, accepted.stderr
    assert accepted.stdout.strip() == "candidate_provenance_ok:count=1"

    missing = candidate._replace(normalized_name="not-installed")
    rejected_missing = _run_provenance(artifact, venv_python, missing, cwd=tmp_path)
    assert rejected_missing.returncode != 0
    assert "candidate_provenance_invalid:installed_count:not-installed:0" in (
        rejected_missing.stdout + rejected_missing.stderr
    )

    direct_url = next(venv.glob("lib/python*/site-packages/demo_pkg-1.0.dist-info/direct_url.json"))
    payload = json.loads(direct_url.read_text(encoding="utf-8"))
    payload["url"] = "https://packages.example.invalid/demo_pkg-1.0-py3-none-any.whl"
    direct_url.write_text(json.dumps(payload), encoding="utf-8")
    rejected_public = _run_provenance(artifact, venv_python, candidate, cwd=tmp_path)
    assert rejected_public.returncode != 0
    assert "candidate_provenance_invalid:source:demo-pkg" in (
        rejected_public.stdout + rejected_public.stderr
    )

    payload["url"] = candidate.uri
    payload["archive_info"] = {"hashes": {"sha256": "0" * 64}}
    direct_url.write_text(json.dumps(payload), encoding="utf-8")
    rejected_hash = _run_provenance(artifact, venv_python, candidate, cwd=tmp_path)
    assert rejected_hash.returncode != 0
    assert "candidate_provenance_invalid:sha256:demo-pkg" in (
        rejected_hash.stdout + rejected_hash.stderr
    )

    direct_url.write_text("{", encoding="utf-8")
    rejected_malformed = _run_provenance(artifact, venv_python, candidate, cwd=tmp_path)
    assert rejected_malformed.returncode != 0
    assert "candidate_provenance_invalid:direct_url_malformed:demo-pkg" in (
        rejected_malformed.stdout + rejected_malformed.stderr
    )


def test_artifact_selection_requires_exactly_one_wheel_and_sdist(tmp_path: Path) -> None:
    artifact = _load_artifact()
    wheel = tmp_path / "rexecop-1.0.0-py3-none-any.whl"
    sdist = tmp_path / "rexecop-1.0.0.tar.gz"
    wheel.touch()
    sdist.touch()

    assert artifact._resolve_artifacts(tmp_path) == (wheel, sdist)
    assert artifact._resolve_wheel(tmp_path) == wheel


@pytest.mark.parametrize(
    ("artifacts", "error"),
    [
        (["rexecop-1.0.0-py3-none-any.whl"], "no_sdist"),
        (["rexecop-1.0.0.tar.gz"], "no_wheel"),
        (
            [
                "rexecop-1.0.0-py3-none-any.whl",
                "rexecop-1.0.1-py3-none-any.whl",
                "rexecop-1.0.0.tar.gz",
            ],
            "ambiguous_wheel",
        ),
        (
            [
                "rexecop-1.0.0-py3-none-any.whl",
                "rexecop-1.0.0.tar.gz",
                "rexecop-1.0.1.tar.gz",
            ],
            "ambiguous_sdist",
        ),
    ],
)
def test_artifact_selection_rejects_missing_or_ambiguous_artifacts(
    tmp_path: Path,
    artifacts: list[str],
    error: str,
) -> None:
    artifact = _load_artifact()
    for filename in artifacts:
        (tmp_path / filename).touch()

    with pytest.raises(SystemExit, match=error):
        artifact._resolve_artifacts(tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="scoped process groups require POSIX")
def test_artifact_runner_preserves_normal_returncodes_and_output(tmp_path: Path) -> None:
    artifact = _load_artifact()
    normal = artifact._run(
        [
            sys.executable,
            "-c",
            "import sys; print('normal-out'); "
            "print('normal-err', file=sys.stderr); raise SystemExit(7)",
        ],
        cwd=tmp_path,
    )
    signalled = artifact._run(
        [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"],
        cwd=tmp_path,
    )

    assert (normal.returncode, normal.stdout, normal.stderr) == (
        7,
        "normal-out\n",
        "normal-err\n",
    )
    assert signalled.returncode == -signal.SIGTERM


@pytest.mark.skipif(os.name != "posix", reason="scoped process groups require POSIX")
def test_artifact_runner_caps_retained_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _load_artifact()
    monkeypatch.setattr(artifact, "_CAPTURE_MAX_BYTES", 32)

    result = artifact._run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x' * 4096); sys.stderr.write('y' * 4096)",
        ],
        cwd=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout == "x" * 32
    assert result.stderr == "y" * 32


@pytest.mark.skipif(os.name != "posix", reason="scoped process groups require POSIX")
def test_artifact_runner_timeout_reserves_cleanup_and_rejects_late_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _load_artifact()
    start = time.monotonic()
    result = artifact._run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        timeout_seconds=0.05,
        terminate_grace_seconds=0.05,
        kill_grace_seconds=0.05,
        outer_deadline=start + 0.35,
    )
    elapsed = time.monotonic() - start

    assert result.returncode == artifact._TIMEOUT_RETURN_CODE
    assert result.stderr.startswith("command_timed_out\n")
    assert elapsed < 0.5

    monkeypatch.setattr(
        artifact.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("late stage must not start"),
    )
    with pytest.raises(RuntimeError, match="no command work budget"):
        artifact._run(
            [sys.executable, "-c", "raise SystemExit(0)"],
            cwd=tmp_path,
            terminate_grace_seconds=0.1,
            kill_grace_seconds=0.1,
            outer_deadline=time.monotonic() + 0.01,
        )


@pytest.mark.skipif(os.name != "posix", reason="scoped process groups require POSIX")
def test_artifact_runner_uses_term_then_kill_only_for_live_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _load_artifact()
    observed: list[int] = []
    original_killpg = artifact.os.killpg

    def record_killpg(group_id: int, signal_number: int) -> None:
        observed.append(signal_number)
        original_killpg(group_id, signal_number)

    monkeypatch.setattr(artifact.os, "killpg", record_killpg)
    graceful = artifact._run(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, lambda *_: exit(0)); time.sleep(30)",
        ],
        cwd=tmp_path,
        timeout_seconds=0.1,
        terminate_grace_seconds=0.2,
        kill_grace_seconds=0.1,
    )

    assert graceful.returncode == artifact._TIMEOUT_RETURN_CODE
    assert observed == [signal.SIGTERM]

    observed.clear()
    stubborn = artifact._run(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        ],
        cwd=tmp_path,
        timeout_seconds=0.1,
        terminate_grace_seconds=0.05,
        kill_grace_seconds=0.1,
    )

    assert stubborn.returncode == artifact._TIMEOUT_RETURN_CODE
    assert observed == [signal.SIGTERM, signal.SIGKILL]


def test_artifact_runner_uses_direct_child_when_group_is_not_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _load_artifact()
    calls: list[str] = []

    class Process:
        pid = 123

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            calls.append("terminate")

        def kill(self) -> None:
            calls.append("kill")

    monkeypatch.setattr(artifact.os, "getpgid", lambda _pid: os.getpgrp())
    monkeypatch.setattr(artifact.os, "killpg", lambda *_args: calls.append("killpg"))

    artifact._signal_live_target(Process(), signal.SIGTERM)

    assert calls == ["terminate"]


def test_artifact_runner_never_group_signals_observed_exited_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _load_artifact()
    calls: list[str] = []

    class Process:
        pid = 123

        def poll(self) -> int:
            return 0

        def terminate(self) -> None:
            calls.append("terminate")

        def kill(self) -> None:
            calls.append("kill")

    monkeypatch.setattr(artifact.os, "killpg", lambda *_args: calls.append("killpg"))

    artifact._signal_live_target(Process(), signal.SIGTERM)

    assert calls == []


@pytest.mark.skipif(os.name != "posix", reason="scoped process groups require POSIX")
def test_artifact_runner_scrubs_environment_and_uses_supplied_empty_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _load_artifact()
    monkeypatch.setenv("PYTHONPATH", "must-not-leak")
    for variable in ("REXECOP_ROOT", "REXECOP_INSTANCE", "REXECOP_STORAGE"):
        monkeypatch.setenv(variable, "must-not-leak")
    empty_cwd = tmp_path / "empty-cwd"
    empty_cwd.mkdir()

    result = artifact._run(
        [
            sys.executable,
            "-c",
            "import os; print(os.getcwd()); "
            "print('|'.join(str(name in os.environ) for name in "
            "('PYTHONPATH', 'REXECOP_ROOT', 'REXECOP_INSTANCE', 'REXECOP_STORAGE')))",
        ],
        cwd=empty_cwd,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [str(empty_cwd), "False|False|False|False"]


def test_artifact_build_output_is_external_to_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _load_artifact()
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, "", "build failed")

    monkeypatch.setattr(artifact, "_run", fake_run)
    monkeypatch.setattr(sys, "argv", [str(ARTIFACT_SCRIPT), "--build"])

    assert artifact.main() == 1
    build = commands[0]
    assert build[:3] == [sys.executable, "-m", "build"]
    assert Path(build[build.index("--outdir") + 1]).is_relative_to(Path("/tmp"))


def test_main_runs_once_per_artifact_in_separate_external_workspaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _load_artifact()
    wheel = tmp_path / "rexecop-1.0.0-py3-none-any.whl"
    sdist = tmp_path / "rexecop-1.0.0.tar.gz"
    wheel.touch()
    sdist.touch()
    calls: list[tuple[Path, str, Path]] = []

    def fake_workflow(
        selected: Path,
        *,
        artifact_kind: str,
        workspace: Path,
        **_kwargs: object,
    ) -> int:
        calls.append((selected, artifact_kind, workspace))
        return 0

    monkeypatch.setattr(artifact, "_run_installed_workflow", fake_workflow)
    monkeypatch.setattr(sys, "argv", [str(ARTIFACT_SCRIPT), "--dist", str(tmp_path)])

    assert artifact.main() == 0
    assert [(selected, kind) for selected, kind, _workspace in calls] == [
        (wheel, "wheel"),
        (sdist, "sdist"),
    ]
    workspaces = [workspace for _selected, _kind, workspace in calls]
    assert len(set(workspaces)) == 2
    assert all(workspace.is_relative_to(Path("/tmp")) for workspace in workspaces)


def test_supply_chain_installer_uses_compatibility_wheel_selector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _load()
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    wheel = dist_dir / "rexecop-1.0.0-py3-none-any.whl"
    wheel.touch()
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(gate, "_run", fake_run)

    venv, venv_python = gate.install_wheel_venv(dist_dir, tmp_path / "install-root")

    assert venv == tmp_path / "install-root" / "venv"
    assert venv_python == venv / "bin" / "python"
    assert len(commands) == 3
    assert "--constraint" not in commands[1]
    assert "--find-links" not in commands[1]
    assert commands[1][-1] == str(wheel.resolve())


def test_supply_chain_installer_reuses_candidate_constraint_and_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _load()
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    wheel = dist_dir / "rexecop-1.0.0-py3-none-any.whl"
    wheel.touch()
    wheelhouse = tmp_path / "candidate-wheels"
    _write_wheel(wheelhouse, "govengine", "1.0.0rc2")
    install_root = tmp_path / "install-root"
    install_root.mkdir()
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(gate, "_run", fake_run)

    gate.install_wheel_venv(
        dist_dir,
        install_root,
        candidate_wheel_dirs=[wheelhouse],
    )

    install = commands[1]
    assert "--find-links" not in install
    assert "--constraint" in install
    constraint = Path(install[install.index("--constraint") + 1])
    assert constraint == install_root / "candidate-constraints.txt"
    assert "govengine @ file://" in constraint.read_text(encoding="utf-8")
    assert "#sha256=" in constraint.read_text(encoding="utf-8")
    provenance = commands[2]
    assert provenance[1:3] == ["-c", _load_artifact()._CANDIDATE_PROVENANCE_CHECK]
    assert commands[3][-2:] == ["pip", "check"]


def test_supply_chain_gate_filters_documented_exceptions() -> None:
    gate = _load()
    findings = [
        {"id": "GHSA-aaaa-bbbb-cccc", "name": "demo", "version": "1.0.0"},
        {"id": "GHSA-dddd-eeee-ffff", "name": "other", "version": "2.0.0"},
    ]
    filtered = gate.filter_findings(findings, {"GHSA-aaaa-bbbb-cccc"})
    assert filtered == [{"id": "GHSA-dddd-eeee-ffff", "name": "other", "version": "2.0.0"}]


def test_supply_chain_gate_rejects_unallowlisted_vulnerability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _load()
    exceptions = tmp_path / "exceptions.json"
    exceptions.write_text(
        json.dumps({"schema": gate.EXCEPTIONS_SCHEMA, "vulnerabilities": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gate,
        "audit_requirements",
        lambda *_args, **_kwargs: [
            {"id": "GHSA-dddd-eeee-ffff", "name": "other", "version": "2.0.0"},
        ],
    )
    monkeypatch.setattr(
        gate,
        "install_wheel_venv",
        lambda *_args, **_kwargs: (tmp_path / "venv", tmp_path / "venv/bin/python"),
    )
    monkeypatch.setattr(
        gate,
        "_run",
        lambda command, **_kwargs: type(
            "Result", (), {"returncode": 0, "stdout": "demo==1.0.0\n", "stderr": ""}
        )(),
    )
    monkeypatch.setattr(gate, "generate_sbom", lambda *_args, **_kwargs: None)

    errors = gate.collect_errors(tmp_path, exceptions_path=exceptions, write_sbom=True)

    assert any(
        error.startswith("unallowlisted_vulnerability:GHSA-dddd-eeee-ffff") for error in errors
    )


def test_supply_chain_gate_cli_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _load()
    version = gate.project_version()
    sbom = tmp_path / f"rexecop-{version}.cdx.json"
    captured: dict[str, object] = {}

    def collect_errors(*_args: object, **kwargs: object) -> list[str]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(gate, "collect_errors", collect_errors)
    monkeypatch.setattr(gate, "sbom_output_path", lambda *_args, **_kwargs: sbom)
    candidate = tmp_path / "candidate-wheels"

    assert gate.main([str(tmp_path), "--candidate-wheel-dir", str(candidate)]) == 0
    assert captured["candidate_wheel_dirs"] == [candidate]
