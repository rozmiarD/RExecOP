#!/usr/bin/env python3
"""Post-publish public-index release gate for rexecop[tecrax] on PyPI.

Runs clean PyPI install smoke, CLI version/doctor checks, optional release
evidence recording, and emits a release-train marker for preflight --post-publish.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from release_evidence import (  # noqa: E402
    ATTESTATION_PREDICATE_TYPE,
    distribution_digests,
    load_record,
    pypi_release_digests,
    sbom_descriptor,
    validate_record,
    write_record,
)
from release_evidence import (  # noqa: E402
    SCHEMA as RELEASE_EVIDENCE_SCHEMA,
)

RELEASE_EVIDENCE_DIR = ROOT / "docs" / "release-evidence"

_CLEAN_INSTALL = ROOT / "scripts" / "validate_clean_install_smoke.py"
_PREFLIGHT = ROOT / "scripts" / "validate_release_train_preflight.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable_to_load_module:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_cli_json(command: list[str], *, cwd: Path) -> dict:
    clean_install = _load_module("rexecop_validate_clean_install_smoke", _CLEAN_INSTALL)
    result = clean_install._run(command, cwd=cwd)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "cli_failed"
        raise RuntimeError(f"{' '.join(command)}:{message}")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{' '.join(command)}:expected_json_object")
    return payload


def run_public_index_checks(
    version: str,
    *,
    no_tecrax_extra: bool = False,
    tmp_parent: Path | None = None,
) -> dict[str, Any]:
    clean_install = _load_module("rexecop_validate_clean_install_smoke", _CLEAN_INSTALL)
    with clean_install.isolated_pypi_install(
        version,
        no_tecrax_extra=no_tecrax_extra,
        tmp_parent=tmp_parent,
    ) as (venv, venv_python, rexecop_bin):
        surface_marker = clean_install.run_surface_smoke(venv_python, version)

        version_result = clean_install._run([str(rexecop_bin), "version"], cwd=venv)
        if version_result.returncode != 0:
            raise RuntimeError(version_result.stderr.strip() or "rexecop_version_failed")
        reported_version = version_result.stdout.strip()
        if reported_version != version:
            raise RuntimeError(f"rexecop_version_mismatch:{reported_version}!={version}")

        runtime_root = venv / "runtime"
        init_result = clean_install._run(
            [str(rexecop_bin), "--root", str(runtime_root), "init"],
            cwd=venv,
        )
        if init_result.returncode != 0:
            raise RuntimeError(init_result.stderr.strip() or "rexecop_init_failed")

        doctor = _run_cli_json(
            [str(rexecop_bin), "--json", "--root", str(runtime_root), "doctor"],
            cwd=venv,
        )
        if doctor.get("status") == "blocker":
            raise RuntimeError(f"rexecop_doctor_blocker:{','.join(doctor.get('blockers') or [])}")

        versions_result = clean_install._run(
            [
                str(venv_python),
                "-c",
                (
                    "import importlib.metadata as m,json;"
                    "print(json.dumps({n:m.version(n) for n in "
                    "('rexecop','govengine','sclite-core','tecrax')}))"
                ),
            ],
            cwd=venv,
        )
        if versions_result.returncode != 0:
            raise RuntimeError(versions_result.stderr.strip() or "installed_versions_failed")
        installed_versions = json.loads(versions_result.stdout)

    return {
        "surface_marker": surface_marker,
        "version": reported_version,
        "doctor_status": str(doctor.get("status", "")),
        "installed_versions": installed_versions,
    }


def release_marker(version: str) -> str:
    clean_install_marker = f"clean_install_smoke_ok:rexecop=={version}"
    return f"public_index_release_smoke_ok:rexecop=={version}:{clean_install_marker}"


def write_release_evidence(
    version: str,
    details: dict[str, Any],
    *,
    dist_dir: Path,
    output: Path,
    source_commit: str,
    workflow_run_id: str,
    workflow_run_url: str,
    sbom_path: Path,
    attestation_id: str,
    attestation_url: str,
    supersedes: str = "",
    public_artifacts: dict[str, str] | None = None,
) -> Path:
    artifacts = distribution_digests(dist_dir)
    public = public_artifacts or pypi_release_digests("rexecop", version)
    if artifacts != public:
        raise ValueError("release_evidence_public_artifact_identity_mismatch")
    sbom = sbom_descriptor(sbom_path)
    attested_subjects = dict(artifacts)
    attested_subjects[str(sbom["filename"])] = str(sbom["sha256"])
    record = {
        "schema": RELEASE_EVIDENCE_SCHEMA,
        "status": "passed",
        "version": version,
        "recorded_at": datetime.now(UTC).isoformat(),
        "source_commit": source_commit,
        "workflow_run_id": workflow_run_id,
        "workflow_run_url": workflow_run_url,
        "artifacts": artifacts,
        "public_artifacts": public,
        "sbom": sbom,
        "attestation": {
            "id": attestation_id,
            "url": attestation_url,
            "predicate_type": ATTESTATION_PREDICATE_TYPE,
            "subjects": attested_subjects,
        },
        "installed_versions": details["installed_versions"],
        "doctor_status": details["doctor_status"],
        "surface_marker": details["surface_marker"],
        "supersedes": supersedes,
    }
    path = write_record(output, record)
    errors = validate_record(load_record(path), expected_version=version)
    if errors:
        raise ValueError(",".join(errors))
    return path


def collect_errors(
    *,
    version: str,
    post_publish: bool,
    evidence_path: Path | None = None,
) -> list[str]:
    if not post_publish:
        return []
    preflight = _load_module("rexecop_validate_release_train_preflight", _PREFLIGHT)
    return preflight.collect_errors(post_publish=True, current_evidence=evidence_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Public-index release smoke gate for rexecop.")
    parser.add_argument("--version", default="", help="Published version (defaults to pyproject).")
    parser.add_argument("--no-tecrax-extra", action="store_true")
    parser.add_argument(
        "--write-evidence",
        action="store_true",
        help="Write a digest-bound JSON release evidence record.",
    )
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--sbom", type=Path, default=None)
    parser.add_argument("--attestation-id", default="")
    parser.add_argument("--attestation-url", default="")
    parser.add_argument("--evidence-output", type=Path, default=None)
    parser.add_argument("--source-commit", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--workflow-run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    parser.add_argument(
        "--workflow-run-url",
        default=(
            f"{os.environ.get('GITHUB_SERVER_URL', '')}/"
            f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/"
            f"{os.environ.get('GITHUB_RUN_ID', '')}"
        ),
    )
    parser.add_argument("--supersedes", default="")
    parser.add_argument(
        "--verify-post-publish",
        action="store_true",
        help="After smoke/evidence, run validate_release_train_preflight.py --post-publish.",
    )
    args = parser.parse_args(argv)

    clean_install = _load_module("rexecop_validate_clean_install_smoke", _CLEAN_INSTALL)
    version = args.version or clean_install.project_version()
    try:
        details = run_public_index_checks(version, no_tecrax_extra=args.no_tecrax_extra)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.write_evidence:
        evidence_output = args.evidence_output or RELEASE_EVIDENCE_DIR / f"{version}.json"
        sbom_path = args.sbom or args.dist_dir / f"rexecop-{version}.cdx.json"
        try:
            evidence_path = write_release_evidence(
                version,
                details,
                dist_dir=args.dist_dir,
                output=evidence_output,
                source_commit=args.source_commit,
                workflow_run_id=args.workflow_run_id,
                workflow_run_url=args.workflow_run_url,
                sbom_path=sbom_path,
                attestation_id=args.attestation_id,
                attestation_url=args.attestation_url,
                supersedes=args.supersedes,
            )
        except (KeyError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"release_evidence_written:{evidence_path}", flush=True)
    else:
        evidence_path = None

    if args.verify_post_publish:
        errors = collect_errors(
            version=version,
            post_publish=True,
            evidence_path=evidence_path,
        )
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1

    print(release_marker(version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
