#!/usr/bin/env python
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from rexecop import __version__


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rexecop-first-run-") as tmp:
        empty_cwd = Path(tmp) / "empty-cwd"
        empty_cwd.mkdir()
        version = _run(empty_cwd, "version").strip()
        if version != __version__:
            raise SystemExit(f"unexpected CLI version: {version}")
        demo = empty_cwd / "first-run-demo"
        materialized = _json(
            empty_cwd,
            "examples",
            "materialize",
            "--output",
            str(demo),
        )
        if materialized.get("status") != "materialized" or materialized.get("files") != [
            "catalog.yaml",
            "environment.yaml",
            "profile/connectors/fixture.yaml",
            "profile/docs/inspect.md",
            "profile/intents/inspect.yaml",
            "profile/profile.yaml",
            "profile/validation_rules/inspect.yaml",
            "profile/workflows/inspect.yaml",
        ]:
            raise SystemExit(f"unexpected materialization payload: {materialized}")
        profile = demo / "profile" / "profile.yaml"
        environment = demo / "environment.yaml"
        catalog = demo / "catalog.yaml"
        runtime_root = empty_cwd / "runtime"
        init = _json(empty_cwd, "--root", str(runtime_root), "init", "--guided")
        if (
            init.get("status") != "initialized"
            or init.get("guided") is not True
            or init.get("secrets_created") is not False
            or not any("examples materialize" in str(step) for step in init.get("next_steps", []))
        ):
            raise SystemExit(f"unexpected guided init payload: {init}")
        doctor = _json(
            empty_cwd,
            "--root",
            str(runtime_root),
            "doctor",
            "--profile",
            str(profile),
            "--env",
            str(environment),
            "--catalog",
            str(catalog),
        )
        if (
            doctor["status"] != "passed"
            or doctor["blockers"]
            or doctor["warnings"]
            or doctor["security_blockers"]
        ):
            raise SystemExit(f"doctor did not pass: {doctor}")
        profile_lint = _json(
            empty_cwd,
            "profile",
            "lint",
            "--profile",
            str(profile),
            "--track",
            "readonly",
        )
        if profile_lint.get("status") != "passed":
            raise SystemExit(f"profile lint did not pass: {profile_lint}")
        environment_lint = _json(
            empty_cwd,
            "env",
            "lint",
            "--env",
            str(environment),
            "--profile",
            str(profile),
        )
        if environment_lint.get("status") != "passed":
            raise SystemExit(f"environment lint did not pass: {environment_lint}")
        explain = _json(
            empty_cwd,
            "operations",
            "explain",
            "inspect",
            "--profile",
            str(profile),
        )
        descriptor = _operation_descriptor_payload(explain)
        if descriptor["id"] != "inspect" or descriptor["side_effect_class"] != "none":
            raise SystemExit(f"unexpected explain payload: {explain}")
        operation_id = _run(
            empty_cwd,
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
        ).strip()
        if not operation_id.startswith("op-"):
            raise SystemExit(f"unexpected operation id: {operation_id}")
        operation_explain = _json(
            empty_cwd,
            "--root",
            str(runtime_root),
            "operation",
            "explain",
            "--operation",
            operation_id,
        )
        if operation_explain.get("schema") != "rexecop.operation_explain.v0.1":
            raise SystemExit(f"unexpected operation explain schema: {operation_explain}")
        operation_review = _json(
            empty_cwd,
            "--root",
            str(runtime_root),
            "operation",
            "review",
            "--operation",
            operation_id,
        )
        if operation_review.get("schema") != "rexecop.operation_review.v0.1":
            raise SystemExit(f"unexpected operation review schema: {operation_review}")
        if operation_review.get("status") != "proceed":
            raise SystemExit(f"operation review did not proceed: {operation_review}")
        runbook = _json(
            empty_cwd,
            "runbook",
            "show",
            "inspect",
            "--profile",
            str(profile),
        )
        if runbook.get("schema") != "rexecop.runbook_show.v0.1":
            raise SystemExit(f"unexpected runbook schema: {runbook}")
        if runbook.get("runbook_ref") != "docs/inspect.md":
            raise SystemExit(f"unexpected runbook ref: {runbook}")
        print(f"first_run_smoke_ok:root={runtime_root}")
    return 0


def _operation_descriptor_payload(payload: dict[str, object]) -> dict[str, object]:
    operation = payload.get("operation")
    if isinstance(operation, dict):
        return operation
    return payload


def _json(cwd: Path, *args: str) -> dict[str, object]:
    output = _run(cwd, *args)
    payload = json.loads(output)
    if not isinstance(payload, dict):
        raise SystemExit(f"expected JSON object from {' '.join(args)}")
    return payload


def _run(cwd: Path, *args: str) -> str:
    cmd = [sys.executable, "-m", "rexecop.cli", *args]
    result = subprocess.run(
        cmd,
        cwd=cwd,
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
