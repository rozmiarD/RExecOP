#!/usr/bin/env python3
"""Install an artifact outside the repository and exercise its public surfaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INSTALLED_SURFACE_SMOKE = """\
import rexecop
from govengine import admit_typed_execution, project_governance_trace
from rexecop.cli_contracts import CLI_CONTRACT_REGISTRY_SCHEMA, cli_contract_registry
from rexecop.cli_errors import CLI_ERROR_SCHEMA
from rexecop.observability.diagnostics import RUNTIME_DIAGNOSTICS_SCHEMA
from rexecop.observability.structured_log import STRUCTURED_LOG_EVENT_SCHEMA
from rexecop.public_api import PUBLIC_API_SCHEMA, public_api_manifest
from rexecop.truth_path import project_truth_path

version = {version!r}
assert rexecop.__version__ == version
registry = cli_contract_registry()
public_api = public_api_manifest()
assert registry["schema"] == CLI_CONTRACT_REGISTRY_SCHEMA
assert public_api["schema"] == PUBLIC_API_SCHEMA
assert registry["contract_count"] >= 17
assert CLI_ERROR_SCHEMA == "rexecop.cli_error.v0.1"
assert STRUCTURED_LOG_EVENT_SCHEMA == "rexecop.structured_log_event.v0.1"
assert RUNTIME_DIAGNOSTICS_SCHEMA == "rexecop.runtime_diagnostics.v0.1"
assert callable(project_truth_path)
assert callable(admit_typed_execution)
assert callable(project_governance_trace)
print(
    "artifact_install_smoke_ok:"
    f"rexecop=={{version}}:"
    f"contracts={{registry['contract_count']}}:"
    "m6_m7_m8=ok"
)
"""

_CAPTURE_MAX_BYTES = 16 * 1024
_COMMAND_TIMEOUT_SECONDS = 60.0
_TERMINATE_GRACE_SECONDS = 2.0
_KILL_GRACE_SECONDS = 1.0
_REAP_GRACE_SECONDS = 0.2
_BUILD_TIMEOUT_SECONDS = 180.0
_VENV_TIMEOUT_SECONDS = 120.0
_PIP_TIMEOUT_SECONDS = 180.0
_SURFACE_TIMEOUT_SECONDS = 30.0
_CLI_TIMEOUT_SECONDS = 30.0
_ARTIFACT_WORKFLOW_TIMEOUT_SECONDS = 600.0
_TIMEOUT_RETURN_CODE = 124
_FIRST_RUN_FILES = (
    "catalog.yaml",
    "environment.yaml",
    "profile/connectors/fixture.yaml",
    "profile/docs/inspect.md",
    "profile/intents/inspect.yaml",
    "profile/profile.yaml",
    "profile/validation_rules/inspect.yaml",
    "profile/workflows/inspect.yaml",
)
_FIRST_RUN_SHA256 = {
    "catalog.yaml": "7ab006ba6750ce09913d298d8fef4ff8d0a33f910e47c2b418bcd86e8f0eda4d",
    "environment.yaml": "8df523efeec68e74ed8a3adb245679213e7f5c187ab1ea5c89210e093ddeaf68",
    "profile/connectors/fixture.yaml": (
        "25b9ba82e7deda6da5a9ae16147640309b0710645e98dd51f70622384d15a368"
    ),
    "profile/docs/inspect.md": "3d0543e05adf6feefdc9f731b29930cd9bf8af7a7ca4b758c88caba4f27e02f0",
    "profile/intents/inspect.yaml": (
        "534968a22865e6b3ae0f6991df841ba0a1921e6d61b52575e1f03c031686e1af"
    ),
    "profile/profile.yaml": "7781e0dbc8222e90bc9d12cdb8575dcff7b177fc323ef23c01e3ade9647ce57f",
    "profile/validation_rules/inspect.yaml": (
        "fa085678d53e921ad856e01b2b8e538737bc8d8b519ba79c55d0c73eeffa1aca"
    ),
    "profile/workflows/inspect.yaml": (
        "20ba5114e9a452990ec5b74cc76f3b8c0a3282d5a702bba2ca12ce62a39d0a17"
    ),
}


def _python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _append_capped(output: bytearray, data: bytes) -> None:
    remaining = _CAPTURE_MAX_BYTES - len(output)
    if remaining > 0:
        output.extend(data[:remaining])


def _read_capped(handle: object) -> bytes:
    getattr(handle, "seek")(0)
    return getattr(handle, "read")(_CAPTURE_MAX_BYTES)


def _prepend_timeout_marker(stderr: bytearray) -> None:
    marker = b"command_timed_out\n"
    stderr[:] = marker + stderr[: _CAPTURE_MAX_BYTES - len(marker)]


def _live_group_id(process: subprocess.Popen[bytes]) -> int | None:
    if process.poll() is not None:
        return None
    try:
        group_id = os.getpgid(process.pid)
    except OSError:
        return None
    if group_id != process.pid or group_id <= 1 or group_id == os.getpgrp():
        return None
    if process.poll() is not None:
        return None
    return group_id


def _signal_live_target(process: subprocess.Popen[bytes], signal_number: int) -> None:
    group_id = _live_group_id(process)
    if group_id is not None:
        try:
            os.killpg(group_id, signal_number)
            return
        except ProcessLookupError:
            return
    if process.poll() is not None:
        return
    try:
        if signal_number == signal.SIGKILL:
            process.kill()
        else:
            process.terminate()
    except ProcessLookupError:
        pass


def _wait_until(process: subprocess.Popen[bytes], deadline: float) -> bool:
    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            process.wait(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            continue
    return True


def _cleanup_timeout(
    process: subprocess.Popen[bytes],
    *,
    outer_deadline: float,
    terminate_grace_seconds: float,
    kill_grace_seconds: float,
) -> None:
    _signal_live_target(process, signal.SIGTERM)
    terminate_deadline = min(time.monotonic() + terminate_grace_seconds, outer_deadline)
    if _wait_until(process, terminate_deadline):
        return
    _signal_live_target(process, signal.SIGKILL)
    reap_deadline = min(
        time.monotonic() + kill_grace_seconds + _REAP_GRACE_SECONDS,
        outer_deadline,
    )
    _wait_until(process, reap_deadline)


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float = _COMMAND_TIMEOUT_SECONDS,
    terminate_grace_seconds: float = _TERMINATE_GRACE_SECONDS,
    kill_grace_seconds: float = _KILL_GRACE_SECONDS,
    outer_deadline: float | None = None,
) -> subprocess.CompletedProcess[str]:
    if os.name != "posix":
        raise RuntimeError("artifact install smoke requires POSIX process groups")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    for variable in ("REXECOP_ROOT", "REXECOP_INSTANCE", "REXECOP_STORAGE"):
        env.pop(variable, None)
    cleanup_reserve = terminate_grace_seconds + kill_grace_seconds + _REAP_GRACE_SECONDS
    now = time.monotonic()
    effective_outer_deadline = outer_deadline or now + timeout_seconds + cleanup_reserve
    work_budget = effective_outer_deadline - now - cleanup_reserve
    if work_budget <= 0:
        raise RuntimeError("artifact smoke outer deadline has no command work budget")
    stage_timeout = min(timeout_seconds, work_budget)
    with tempfile.TemporaryFile(mode="w+b", dir="/tmp") as stdout_file, tempfile.TemporaryFile(
        mode="w+b", dir="/tmp"
    ) as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            env=env,
            start_new_session=True,
        )
        timed_out = False
        try:
            process.wait(timeout=stage_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _cleanup_timeout(
                process,
                outer_deadline=effective_outer_deadline,
                terminate_grace_seconds=terminate_grace_seconds,
                kill_grace_seconds=kill_grace_seconds,
            )
        stdout = bytearray(_read_capped(stdout_file))
        stderr = bytearray(_read_capped(stderr_file))
    if timed_out:
        _prepend_timeout_marker(stderr)
        returncode = _TIMEOUT_RETURN_CODE
    else:
        returncode = process.returncode
    return subprocess.CompletedProcess(
        command,
        returncode if returncode is not None else _TIMEOUT_RETURN_CODE,
        bytes(stdout).decode("utf-8", "replace"),
        bytes(stderr).decode("utf-8", "replace"),
    )


def _project_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _select_artifact(dist_dir: Path, pattern: str, kind: str) -> Path:
    artifacts = sorted(dist_dir.glob(pattern))
    if not artifacts:
        raise SystemExit(f"artifact_install_smoke_failed:no_{kind}:{dist_dir}")
    if len(artifacts) != 1:
        raise SystemExit(f"artifact_install_smoke_failed:ambiguous_{kind}:{dist_dir}")
    return artifacts[0]


def _resolve_artifacts(dist_dir: Path) -> tuple[Path, Path]:
    return (
        _select_artifact(dist_dir, "*.whl", "wheel"),
        _select_artifact(dist_dir, "*.tar.gz", "sdist"),
    )


def _resolve_wheel(dist_dir: Path) -> Path:
    return _select_artifact(dist_dir, "*.whl", "wheel")


def _candidate_install_options(candidate_wheel_dirs: Sequence[Path]) -> list[str]:
    options: list[str] = []
    for wheel_dir in candidate_wheel_dirs:
        resolved = wheel_dir.resolve()
        if not resolved.is_dir():
            raise RuntimeError(f"candidate_wheel_dir_missing:{resolved}")
        options.extend(["--find-links", str(resolved)])
    return options


def _rexecop(venv: Path) -> Path:
    return venv / ("Scripts/rexecop.exe" if sys.platform == "win32" else "bin/rexecop")


def _run_installed_workflow(
    artifact: Path,
    *,
    artifact_kind: str,
    workspace: Path,
    python: str,
    version: str,
    candidate_options: list[str],
    stage: Callable[..., subprocess.CompletedProcess[str]],
) -> int:
    venv = workspace / "venv"
    empty_cwd = workspace / "empty-cwd"
    empty_cwd.mkdir()
    create = stage([python, "-m", "venv", str(venv)], cwd=ROOT, timeout=_VENV_TIMEOUT_SECONDS)
    if create.returncode != 0:
        print(create.stderr, file=sys.stderr)
        return create.returncode
    venv_python = str(_python(venv))
    rexecop = str(_rexecop(venv))
    install = stage(
        [
            venv_python,
            "-m",
            "pip",
            "install",
            "-q",
            "--upgrade",
            "pip",
            *candidate_options,
            str(artifact.resolve()),
        ],
        cwd=ROOT,
        timeout=_PIP_TIMEOUT_SECONDS,
    )
    if install.returncode != 0:
        print(install.stdout)
        print(install.stderr, file=sys.stderr)
        return install.returncode
    pip_check = stage(
        [venv_python, "-m", "pip", "check"],
        cwd=ROOT,
        timeout=_SURFACE_TIMEOUT_SECONDS,
    )
    if pip_check.returncode != 0:
        print(pip_check.stdout)
        print(pip_check.stderr, file=sys.stderr)
        return pip_check.returncode
    smoke = stage(
        [venv_python, "-c", INSTALLED_SURFACE_SMOKE.format(version=version)],
        cwd=empty_cwd,
        timeout=_SURFACE_TIMEOUT_SECONDS,
    )
    if smoke.returncode != 0:
        print(smoke.stdout)
        print(smoke.stderr, file=sys.stderr)
        return smoke.returncode
    cli_version = stage(
        [rexecop, "version"],
        cwd=empty_cwd,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    if cli_version.returncode != 0 or cli_version.stdout.strip() != version:
        print("artifact_install_smoke_failed:first_run_cli_version", file=sys.stderr)
        return cli_version.returncode or 1
    demo = empty_cwd / "first-run-demo"
    materialize = stage(
        [rexecop, "examples", "materialize", "--output", str(demo)],
        cwd=empty_cwd,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    if materialize.returncode != 0:
        print(materialize.stdout)
        print(materialize.stderr, file=sys.stderr)
        return materialize.returncode
    materialized = json.loads(materialize.stdout)
    if (
        materialized.get("status") != "materialized"
        or materialized.get("example") != "first-run-demo"
        or materialized.get("fixture_version") != "v0.1.0"
        or materialized.get("files") != list(_FIRST_RUN_FILES)
    ):
        print("artifact_install_smoke_failed:first_run_materialize_output", file=sys.stderr)
        return 1
    actual_files = {
        path.relative_to(demo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in demo.rglob("*")
        if path.is_file()
    }
    if actual_files != _FIRST_RUN_SHA256:
        print("artifact_install_smoke_failed:first_run_materialize_bytes", file=sys.stderr)
        return 1
    runtime_root = empty_cwd / "first-run-runtime"
    init_first_run = stage(
        [rexecop, "--root", str(runtime_root), "init", "--guided"],
        cwd=empty_cwd,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    if init_first_run.returncode != 0:
        print(init_first_run.stdout)
        print(init_first_run.stderr, file=sys.stderr)
        return init_first_run.returncode
    guided_init = json.loads(init_first_run.stdout)
    if (
        guided_init.get("status") != "initialized"
        or guided_init.get("guided") is not True
        or guided_init.get("secrets_created") is not False
        or not any(
            "examples materialize" in str(step)
            for step in guided_init.get("next_steps", [])
        )
    ):
        print("artifact_install_smoke_failed:first_run_guided_init", file=sys.stderr)
        return 1
    profile = demo / "profile" / "profile.yaml"
    environment = demo / "environment.yaml"
    catalog = demo / "catalog.yaml"
    doctor = stage(
        [
            rexecop,
            "--root",
            str(runtime_root),
            "doctor",
            "--profile",
            str(profile),
            "--env",
            str(environment),
            "--catalog",
            str(catalog),
        ],
        cwd=empty_cwd,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    if doctor.returncode != 0:
        print(doctor.stdout)
        print(doctor.stderr, file=sys.stderr)
        return doctor.returncode
    doctor_payload = json.loads(doctor.stdout)
    if (
        doctor_payload.get("status") != "passed"
        or doctor_payload.get("blockers")
        or doctor_payload.get("warnings")
        or doctor_payload.get("security_blockers")
    ):
        print("artifact_install_smoke_failed:first_run_doctor", file=sys.stderr)
        return 1
    for command, label in (
        (
            [
                rexecop,
                "profile",
                "lint",
                "--profile",
                str(profile),
                "--track",
                "readonly",
            ],
            "profile_lint",
        ),
        (
            [rexecop, "env", "lint", "--env", str(environment), "--profile", str(profile)],
            "env_lint",
        ),
    ):
        lint = stage(command, cwd=empty_cwd, timeout=_CLI_TIMEOUT_SECONDS)
        if lint.returncode != 0 or json.loads(lint.stdout).get("status") != "passed":
            print(f"artifact_install_smoke_failed:first_run_{label}", file=sys.stderr)
            return lint.returncode or 1
    explain = stage(
        [rexecop, "operations", "explain", "inspect", "--profile", str(profile)],
        cwd=empty_cwd,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    if explain.returncode != 0:
        print(explain.stdout)
        print(explain.stderr, file=sys.stderr)
        return explain.returncode
    explained = json.loads(explain.stdout)
    operation = explained.get("operation", explained)
    if not isinstance(operation, dict) or operation.get("side_effect_class") != "none":
        print("artifact_install_smoke_failed:first_run_explain", file=sys.stderr)
        return 1
    plan = stage(
        [
            rexecop,
            "--root",
            str(runtime_root),
            "plan",
            "--catalog",
            str(catalog),
            "--intent",
            "inspect",
            "--target",
            "fixture-target",
            "--mode",
            "dry_run",
        ],
        cwd=empty_cwd,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    if plan.returncode != 0 or not plan.stdout.strip().startswith("op-"):
        print("artifact_install_smoke_failed:first_run_plan", file=sys.stderr)
        return plan.returncode or 1
    source = empty_cwd / "source"
    archive = empty_cwd / "runtime-backup.tar"
    sidecar = empty_cwd / "runtime-backup.manifest.json"
    target = empty_cwd / "restored"
    init = stage(
        [rexecop, "--root", str(source), "--json", "init"],
        cwd=empty_cwd,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    if init.returncode != 0:
        print(init.stdout)
        print(init.stderr, file=sys.stderr)
        return init.returncode
    (source / "operations" / "record.json").write_text('{"id": "smoke"}\n', encoding="utf-8")
    backup = stage(
        [
            rexecop,
            "--root",
            str(source),
            "--json",
            "backup",
            "create",
            "--output",
            str(archive),
        ],
        cwd=empty_cwd,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    if backup.returncode != 0:
        print(backup.stdout)
        print(backup.stderr, file=sys.stderr)
        return backup.returncode
    created = json.loads(backup.stdout)
    if created.get("archive") != str(archive) or created.get("manifest") != str(sidecar):
        print("artifact_install_smoke_failed:backup_output_name", file=sys.stderr)
        return 1
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    manifest_members = [str(item["path"]) for item in manifest["files"]]
    with tarfile.open(archive, "r:") as handle:
        archive_members = [member.name for member in handle.getmembers()]
        if (
            archive_members != manifest_members
            or len(set(archive_members)) != len(archive_members)
        ):
            print("artifact_install_smoke_failed:backup_member_set", file=sys.stderr)
            return 1
        if manifest.get("file_count") != len(archive_members):
            print("artifact_install_smoke_failed:backup_member_count", file=sys.stderr)
            return 1
        for member, item in zip(handle.getmembers(), manifest["files"], strict=True):
            extracted = handle.extractfile(member)
            if (
                extracted is None
                or hashlib.sha256(extracted.read()).hexdigest() != item["sha256"]
            ):
                print("artifact_install_smoke_failed:backup_member_digest", file=sys.stderr)
                return 1
    restore = stage(
        [
            rexecop,
            "--root",
            str(target),
            "--json",
            "backup",
            "restore",
            "--archive",
            str(archive),
            "--manifest",
            str(sidecar),
        ],
        cwd=empty_cwd,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    if restore.returncode != 0 or json.loads(restore.stdout).get("status") != "restored":
        print(restore.stdout)
        print(restore.stderr, file=sys.stderr)
        return restore.returncode or 1
    restored_record = (target / "operations" / "record.json").read_text(encoding="utf-8")
    if restored_record != '{"id": "smoke"}\n':
        print("artifact_install_smoke_failed:restored_content", file=sys.stderr)
        return 1
    reopen_script = (
        "from pathlib import Path; from rexecop.storage.factory import create_store; "
        "import sys; root = Path(sys.argv[1]); "
        "assert create_store(root, backend='file').root == root"
    )
    reopen = stage(
        [venv_python, "-c", reopen_script, str(target)],
        cwd=empty_cwd,
        timeout=_SURFACE_TIMEOUT_SECONDS,
    )
    if reopen.returncode != 0:
        print(reopen.stdout)
        print(reopen.stderr, file=sys.stderr)
        return reopen.returncode
    (source / "operations" / "credentials.json").write_text(
        '{"token": "value"}\n', encoding="utf-8"
    )
    blocked_archive = empty_cwd / "blocked.tar"
    blocked_sidecar = empty_cwd / "blocked.manifest.json"
    blocked = stage(
        [
            rexecop,
            "--root",
            str(source),
            "--json",
            "backup",
            "create",
            "--output",
            str(blocked_archive),
        ],
        cwd=empty_cwd,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    blocked_output = blocked.stdout + blocked.stderr
    if (
        blocked.returncode != 1
        or "Traceback" in blocked_output
        or "value" in blocked_output
        or blocked_archive.exists()
        or blocked_sidecar.exists()
    ):
        print("artifact_install_smoke_failed:backup_secret_scan", file=sys.stderr)
        return 1
    print(f"{smoke.stdout.strip()}:artifact={artifact_kind}")
    print(f"artifact_runtime_backup_smoke_ok:artifact={artifact_kind}:members={len(archive_members)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--candidate-wheel-dir", action="append", type=Path, default=[])
    args = parser.parse_args()
    version = _project_version()
    python = sys.executable
    with tempfile.TemporaryDirectory(prefix="rexecop-artifact-smoke-", dir="/tmp") as tmp:
        tmp_root = Path(tmp)
        outer_deadline = time.monotonic() + _ARTIFACT_WORKFLOW_TIMEOUT_SECONDS

        def stage(
            command: list[str], *, cwd: Path, timeout: float
        ) -> subprocess.CompletedProcess[str]:
            return _run(command, cwd=cwd, timeout_seconds=timeout, outer_deadline=outer_deadline)

        dist_dir = args.dist
        if args.build:
            dist_dir = tmp_root / "build-dist"
            build = stage(
                [python, "-m", "build", "--outdir", str(dist_dir)],
                cwd=ROOT,
                timeout=_BUILD_TIMEOUT_SECONDS,
            )
            if build.returncode != 0:
                print(build.stdout)
                print(build.stderr, file=sys.stderr)
                return build.returncode
        try:
            candidate_options = _candidate_install_options(args.candidate_wheel_dir)
        except RuntimeError as exc:
            print(f"artifact_install_smoke_failed:{exc}", file=sys.stderr)
            return 1
        wheel, sdist = _resolve_artifacts(dist_dir)
        for artifact_kind, artifact in (("wheel", wheel), ("sdist", sdist)):
            workspace = tmp_root / artifact_kind
            workspace.mkdir()
            workflow_result = _run_installed_workflow(
                artifact,
                artifact_kind=artifact_kind,
                workspace=workspace,
                python=python,
                version=version,
                candidate_options=candidate_options,
                stage=stage,
            )
            if workflow_result != 0:
                return workflow_result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
