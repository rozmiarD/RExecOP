#!/usr/bin/env python3
"""Run readonly staging http_api lab: local API stub or operator env file."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from helpers.staging_http_server import StagingHttpServer  # noqa: E402

PROFILE = ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
LOCAL_ENV_TEMPLATE = (
    ROOT / "examples/environments/small-public-unit-proxmox.staging.lab.example.yaml"
)


def _local_environment(server_base_url: str) -> dict:
    template = yaml.safe_load(LOCAL_ENV_TEMPLATE.read_text(encoding="utf-8"))
    env = template["environment"]
    for connector in env.get("connectors", {}).values():
        if isinstance(connector, dict) and connector.get("backend") == "http_api":
            connector["base_url"] = server_base_url
            connector.pop("base_url_secret_ref", None)
            auth = connector.get("auth")
            if isinstance(auth, dict):
                connector.pop("auth", None)
    return template


def _run_rexecop(workdir: Path, env_path: Path) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    plan = subprocess.run(
        [
            sys.executable,
            "-m",
            "rexecop.cli",
            "plan",
            "--profile",
            str(PROFILE),
            "--env",
            str(env_path),
            "--intent",
            "check_backup_status",
            "--target",
            "all_critical_vms",
            "--mode",
            "dry_run",
        ],
        cwd=workdir,
        text=True,
        capture_output=True,
        check=True,
    )
    operation_id = plan.stdout.strip().splitlines()[-1].strip()
    start = subprocess.run(
        [
            sys.executable,
            "-m",
            "rexecop.cli",
            "start",
            "--operation",
            operation_id,
        ],
        cwd=workdir,
        text=True,
        capture_output=True,
        check=True,
    )
    validate = subprocess.run(
        [
            sys.executable,
            "-m",
            "rexecop.cli",
            "validate",
            "--operation",
            operation_id,
        ],
        cwd=workdir,
        text=True,
        capture_output=True,
        check=True,
    )
    status = subprocess.run(
        [
            sys.executable,
            "-m",
            "rexecop.cli",
            "status",
            "--operation",
            operation_id,
        ],
        cwd=workdir,
        text=True,
        capture_output=True,
        check=True,
    )
    validation = json.loads(validate.stdout)
    status_payload = json.loads(status.stdout)
    evidence_dir = workdir / ".rexecop" / "evidence" / operation_id
    leaked = False
    if evidence_dir.is_dir():
        blob = "\n".join(path.read_text(encoding="utf-8") for path in evidence_dir.glob("*.json"))
        lowered = blob.lower()
        if "secret-token" in blob or (
            any(token in lowered for token in ("api_key", "password", "bearer "))
            and "[REDACTED]" not in blob
        ):
            leaked = True
    if status_payload.get("state") != "completed":
        raise RuntimeError(f"staging_lab_failed:state={status_payload.get('state')}")
    if not validation.get("passed"):
        raise RuntimeError(f"staging_lab_failed:validate={validation}")
    if leaked:
        raise RuntimeError("staging_lab_failed:secret_leak_in_evidence")
    return {
        "operation_id": operation_id,
        "state": status_payload.get("state"),
        "validation_passed": validation.get("passed"),
        "rule": validation.get("rule"),
        "start_stdout": start.stdout.strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "rexecop-staging-http-lab",
        help="Directory for .rexecop runtime (default: /tmp/rexecop-staging-http-lab)",
    )
    parser.add_argument(
        "--env",
        type=Path,
        help="Operator environment YAML (http_api + secrets). Skips local stub.",
    )
    args = parser.parse_args()

    if args.env is not None:
        if not args.env.is_file():
            print(f"missing_env:{args.env}", file=sys.stderr)
            return 1
        result = _run_rexecop(args.workdir, args.env)
        print(f"staging_http_lab_ok:external_env:{result['operation_id']}")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    server = StagingHttpServer()
    server.start()
    try:
        with tempfile.TemporaryDirectory(prefix="rexecop-staging-env-") as tmp:
            env_path = Path(tmp) / "staging.lab.yaml"
            env_path.write_text(
                yaml.safe_dump(_local_environment(server.base_url)),
                encoding="utf-8",
            )
            result = _run_rexecop(args.workdir, env_path)
    finally:
        server.stop()

    print(f"staging_http_lab_ok:local_stub:{result['operation_id']}")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
