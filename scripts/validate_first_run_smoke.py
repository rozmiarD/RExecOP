#!/usr/bin/env python
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "first-run-demo"
PROFILE = DEMO / "profile" / "profile.yaml"
ENVIRONMENT = DEMO / "environment.yaml"
CATALOG = DEMO / "catalog.yaml"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rexecop-first-run-") as tmp:
        runtime_root = Path(tmp) / "runtime"
        _run("--root", str(runtime_root), "init")
        doctor = _json(
            "--root",
            str(runtime_root),
            "doctor",
            "--profile",
            str(PROFILE),
            "--env",
            str(ENVIRONMENT),
            "--catalog",
            str(CATALOG),
        )
        if doctor["status"] != "passed":
            raise SystemExit(f"doctor did not pass: {doctor}")
        explain = _json(
            "operations",
            "explain",
            "inspect",
            "--profile",
            str(PROFILE),
        )
        if explain["id"] != "inspect" or explain["side_effect_class"] != "none":
            raise SystemExit(f"unexpected explain payload: {explain}")
        operation_id = _run(
            "--root",
            str(runtime_root),
            "plan",
            "--catalog",
            str(CATALOG),
            "--intent",
            "inspect",
            "--target",
            "fixture-target",
            "--mode",
            "dry_run",
        ).strip()
        if not operation_id.startswith("op-"):
            raise SystemExit(f"unexpected operation id: {operation_id}")
        print(f"first_run_smoke_ok:root={runtime_root}")
    return 0


def _json(*args: str) -> dict[str, object]:
    output = _run(*args)
    payload = json.loads(output)
    if not isinstance(payload, dict):
        raise SystemExit(f"expected JSON object from {' '.join(args)}")
    return payload


def _run(*args: str) -> str:
    cmd = [sys.executable, "-m", "rexecop.cli", *args]
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            "command failed: "
            + " ".join(cmd)
            + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


if __name__ == "__main__":
    raise SystemExit(main())
