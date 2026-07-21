#!/usr/bin/env python3
"""Supply-chain gate for built rexecop wheels: pip-audit + CycloneDX SBOM."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXCEPTIONS_PATH = ROOT / "docs" / "supply-chain-audit-exceptions.json"
EXCEPTIONS_SCHEMA = "rexecop.supply_chain_audit_exceptions.v0.1"
ARTIFACT_SMOKE = ROOT / "scripts" / "validate_artifact_install_smoke.py"


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable_to_load_module:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def load_exceptions(path: Path = EXCEPTIONS_PATH) -> set[str]:
    if not path.is_file():
        raise RuntimeError(f"exceptions_file_missing:{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != EXCEPTIONS_SCHEMA:
        raise RuntimeError(f"exceptions_schema_mismatch:{payload.get('schema')}")
    allowed: set[str] = set()
    for item in payload.get("vulnerabilities", []):
        if not isinstance(item, dict):
            continue
        vuln_id = str(item.get("id", "")).strip()
        if vuln_id:
            allowed.add(vuln_id)
    return allowed


def _parse_pip_audit_json(stdout: str) -> list[dict[str, str]]:
    text = stdout.strip()
    if not text:
        return []
    payload = json.loads(text)
    findings: list[dict[str, str]] = []
    for dependency in payload.get("dependencies", []):
        name = str(dependency.get("name", ""))
        version = str(dependency.get("version", ""))
        for vuln in dependency.get("vulns", []):
            findings.append(
                {
                    "id": str(vuln.get("id", "")),
                    "name": name,
                    "version": version,
                }
            )
    return [item for item in findings if item["id"]]


def audit_requirements(
    requirements_file: Path,
    *,
    pip_audit_cmd: list[str] | None = None,
) -> list[dict[str, str]]:
    command = list(pip_audit_cmd or [sys.executable, "-m", "pip_audit"])
    command.extend(["-r", str(requirements_file), "-f", "json"])
    result = _run(command)
    if result.returncode not in {0, 1}:
        message = result.stderr.strip() or result.stdout.strip() or "pip_audit_failed"
        raise RuntimeError(message)
    return _parse_pip_audit_json(result.stdout)


def filter_findings(
    findings: list[dict[str, str]],
    allowed_ids: set[str],
) -> list[dict[str, str]]:
    return [item for item in findings if item["id"] not in allowed_ids]


def sbom_output_path(dist_dir: Path, version: str) -> Path:
    return dist_dir / f"rexecop-{version}.cdx.json"


def generate_sbom(
    venv_python: Path,
    output: Path,
    *,
    cyclonedx_cmd: list[str] | None = None,
) -> None:
    command = list(cyclonedx_cmd or [sys.executable, "-m", "cyclonedx_py"])
    command.extend(["environment", "--of", "JSON", "-o", str(output), str(venv_python)])
    result = _run(command)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "cyclonedx_failed"
        raise RuntimeError(message)
    payload = json.loads(output.read_text(encoding="utf-8"))
    if payload.get("bomFormat") != "CycloneDX":
        raise RuntimeError(f"sbom_invalid_bom_format:{payload.get('bomFormat')}")


def install_wheel_venv(
    dist_dir: Path,
    tmp_dir: Path,
    *,
    host_python: str | None = None,
    candidate_wheel_dirs: Sequence[Path] = (),
) -> tuple[Path, Path]:
    artifact = _load_module("rexecop_validate_artifact_install_smoke", ARTIFACT_SMOKE)
    wheel = artifact._resolve_wheel(dist_dir)
    python = host_python or sys.executable
    venv = tmp_dir / "venv"
    create = _run([python, "-m", "venv", str(venv)], cwd=ROOT)
    if create.returncode != 0:
        raise RuntimeError(create.stderr.strip() or "venv_create_failed")
    venv_python = artifact._python(venv)
    candidate_options = artifact._candidate_install_options(candidate_wheel_dirs)
    install = _run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "-q",
            "--upgrade",
            "pip",
            *candidate_options,
            str(wheel.resolve()),
        ],
        cwd=ROOT,
    )
    if install.returncode != 0:
        message = install.stderr.strip() or install.stdout.strip() or "wheel_install_failed"
        raise RuntimeError(message)
    pip_check = _run([str(venv_python), "-m", "pip", "check"], cwd=ROOT)
    if pip_check.returncode != 0:
        message = pip_check.stderr.strip() or pip_check.stdout.strip() or "pip_check_failed"
        raise RuntimeError(message)
    return venv, venv_python


def collect_errors(
    dist_dir: Path,
    *,
    version: str | None = None,
    pip_audit_cmd: list[str] | None = None,
    cyclonedx_cmd: list[str] | None = None,
    exceptions_path: Path = EXCEPTIONS_PATH,
    write_sbom: bool = True,
    candidate_wheel_dirs: Sequence[Path] = (),
) -> list[str]:
    errors: list[str] = []
    selected_version = version or project_version()
    try:
        allowed = load_exceptions(exceptions_path)
    except RuntimeError as exc:
        return [str(exc)]

    try:
        with tempfile.TemporaryDirectory(prefix="rexecop-supply-chain-") as tmp:
            _venv, venv_python = install_wheel_venv(
                dist_dir,
                Path(tmp),
                candidate_wheel_dirs=candidate_wheel_dirs,
            )
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
                freeze_path = Path(handle.name)
            freeze = _run([str(venv_python), "-m", "pip", "freeze"], cwd=ROOT)
            if freeze.returncode != 0:
                return [freeze.stderr.strip() or "pip_freeze_failed"]
            freeze_path.write_text(freeze.stdout, encoding="utf-8")

            try:
                findings = audit_requirements(freeze_path, pip_audit_cmd=pip_audit_cmd)
            except RuntimeError as exc:
                return [f"pip_audit_failed:{exc}"]
            finally:
                freeze_path.unlink(missing_ok=True)

            unallowlisted = filter_findings(findings, allowed)
            for item in unallowlisted:
                errors.append(
                    f"unallowlisted_vulnerability:{item['id']}:{item['name']}=={item['version']}"
                )

            if write_sbom:
                output = sbom_output_path(dist_dir, selected_version)
                try:
                    generate_sbom(venv_python, output, cyclonedx_cmd=cyclonedx_cmd)
                except (RuntimeError, json.JSONDecodeError, OSError) as exc:
                    errors.append(f"sbom_generation_failed:{exc}")
    except RuntimeError as exc:
        return [f"wheel_install_failed:{exc}"]

    return errors


def success_line(version: str, *, vulnerability_count: int, sbom_path: Path) -> str:
    return (
        f"supply_chain_gate_ok:rexecop=={version}:"
        f"vulnerabilities={vulnerability_count}:"
        f"sbom={sbom_path.name}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run pip-audit and SBOM gate for dist artifacts.")
    parser.add_argument("dist", type=Path, nargs="?", default=ROOT / "dist")
    parser.add_argument("--version", default="", help="Version encoded in the SBOM filename.")
    parser.add_argument("--no-sbom", action="store_true", help="Skip CycloneDX SBOM generation.")
    parser.add_argument(
        "--candidate-wheel-dir",
        action="append",
        type=Path,
        default=[],
        help=(
            "Local wheelhouse used to resolve exact dependency pins before publication; "
            "repeat for multiple directories."
        ),
    )
    args = parser.parse_args(argv)

    dist_dir = args.dist.resolve()
    version = args.version or project_version()
    errors = collect_errors(
        dist_dir,
        version=version,
        write_sbom=not args.no_sbom,
        candidate_wheel_dirs=args.candidate_wheel_dir,
    )
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    sbom_path = sbom_output_path(dist_dir, version)
    print(success_line(version, vulnerability_count=0, sbom_path=sbom_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
