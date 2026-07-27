from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
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


def test_candidate_install_options_use_local_wheelhouse(tmp_path: Path) -> None:
    artifact = _load_artifact()
    wheelhouse = tmp_path / "candidate-wheels"
    wheelhouse.mkdir()

    assert artifact._candidate_install_options([wheelhouse]) == [
        "--find-links",
        str(wheelhouse.resolve()),
    ]


def test_candidate_install_options_reject_missing_wheelhouse(tmp_path: Path) -> None:
    artifact = _load_artifact()

    with pytest.raises(RuntimeError, match="candidate_wheel_dir_missing"):
        artifact._candidate_install_options([tmp_path / "missing"])


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
    assert commands[1][-1] == str(wheel.resolve())


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
