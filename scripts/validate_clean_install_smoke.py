#!/usr/bin/env python3
"""Verify a clean PyPI install of the RExecOP core exposes its supported surfaces."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
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
assert CLI_ERROR_SCHEMA == "rexecop.cli_error.v0.1"
assert STRUCTURED_LOG_EVENT_SCHEMA == "rexecop.structured_log_event.v0.1"
assert RUNTIME_DIAGNOSTICS_SCHEMA == "rexecop.runtime_diagnostics.v0.1"
assert callable(project_truth_path)
assert callable(admit_typed_execution)
assert callable(project_governance_trace)
print(
    "clean_install_smoke_ok:"
    f"rexecop=={{version}}:"
    f"contracts={{registry['contract_count']}}:"
    "pip_check=ok"
)
"""

CLEAN_INSTALL_MARKER_PREFIX = "clean_install_smoke_ok:rexecop=="


def _python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _rexecop_bin(venv: Path) -> Path:
    name = "rexecop.exe" if sys.platform == "win32" else "rexecop"
    return venv / ("Scripts" if sys.platform == "win32" else "bin") / name


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def project_version(root: Path | None = None) -> str:
    base = root or ROOT
    data = tomllib.loads((base / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def clean_install_marker(version: str) -> str:
    return f"{CLEAN_INSTALL_MARKER_PREFIX}{version}"


@contextmanager
def isolated_pypi_install(
    version: str,
    *,
    tmp_parent: Path | None = None,
) -> Iterator[tuple[Path, Path, Path]]:
    """Yield (venv_dir, venv_python, rexecop_bin) after PyPI install and pip check."""
    requirement = f"rexecop=={version}"
    python = sys.executable
    with tempfile.TemporaryDirectory(prefix="rexecop-clean-install-", dir=tmp_parent) as tmp:
        venv = Path(tmp) / "venv"
        create = _run([python, "-m", "venv", str(venv)])
        if create.returncode != 0:
            raise RuntimeError(create.stderr.strip() or "venv_create_failed")

        venv_python = _python(venv)
        install = _run(
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "-q",
                "--upgrade",
                "pip",
                "--pre",
                requirement,
            ]
        )
        if install.returncode != 0:
            message = install.stderr.strip() or install.stdout.strip() or "pip_install_failed"
            raise RuntimeError(message)

        pip_check = _run([str(venv_python), "-m", "pip", "check"])
        if pip_check.returncode != 0:
            message = pip_check.stderr.strip() or pip_check.stdout.strip() or "pip_check_failed"
            raise RuntimeError(message)

        yield venv, venv_python, _rexecop_bin(venv)


def run_surface_smoke(venv_python: Path, version: str) -> str:
    smoke = _run(
        [
            str(venv_python),
            "-c",
            INSTALLED_SURFACE_SMOKE.format(version=version),
        ]
    )
    if smoke.returncode != 0:
        message = smoke.stderr.strip() or smoke.stdout.strip() or "surface_smoke_failed"
        raise RuntimeError(message)
    marker = smoke.stdout.strip()
    expected = clean_install_marker(version)
    if not marker.startswith(expected):
        raise RuntimeError(f"unexpected_surface_marker:{marker}")
    return marker


def run_clean_install_smoke(version: str) -> str:
    with isolated_pypi_install(version) as (
        _venv,
        venv_python,
        _bin,
    ):
        return run_surface_smoke(venv_python, version)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--version",
        default="",
        help="Package version to install (defaults to pyproject version).",
    )
    args = parser.parse_args()

    version = args.version or project_version()
    try:
        marker = run_clean_install_smoke(version)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(marker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
