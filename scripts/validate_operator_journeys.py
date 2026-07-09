#!/usr/bin/env python
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = ROOT / "examples/environments/runtime-fixture.example.yaml"
POLICY_ENVIRONMENT = ROOT / "examples/environments/runtime-fixture.policy.example.yaml"
FIRST_RUN_PROFILE = ROOT / "examples/first-run-demo/profile/profile.yaml"
FIRST_RUN_ENVIRONMENT = ROOT / "examples/first-run-demo/environment.yaml"
CATALOG = ROOT / "examples/first-run-demo/catalog.yaml"

FAILURE_ENV = json.dumps(
    {
        "fixture_source:read_fixture_state": {
            "count": 5,
            "error_class": "transient_connector_error",
        }
    }
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rexecop-operator-journey-") as tmp:
        runtime_root = Path(tmp) / "runtime"
        failure_root = Path(tmp) / "failure-runtime"
        _run_readonly_journey(runtime_root)
        _run_failure_journey(failure_root)
        _run_governance_journey()
        _run_audit_journey(runtime_root)
    print(
        "operator_journeys_ok:"
        "readonly=OK,failure=OK,governance=OK,audit=OK"
    )
    return 0


def _run_readonly_journey(runtime_root: Path) -> None:
    _run("--root", str(runtime_root), "init")
    env_lint = _json(
        "env",
        "lint",
        "--env",
        str(ENVIRONMENT),
        "--profile",
        str(PROFILE),
    )
    if env_lint.get("status") != "passed":
        raise SystemExit(f"env lint failed: {env_lint}")
    profile_harness = _json(
        "profile",
        "harness",
        "--profile",
        str(PROFILE),
    )
    if profile_harness.get("status") != "passed":
        raise SystemExit(f"profile harness failed: {profile_harness}")
    secrets = _json(
        "secrets",
        "doctor",
        "--env",
        str(ENVIRONMENT),
        "--catalog",
        str(CATALOG),
    )
    if secrets.get("status") not in {"passed", "warning"}:
        raise SystemExit(f"secrets doctor failed: {secrets}")
    preview = _json(
        "action",
        "preview",
        "inspect_fixture_state",
        "--profile",
        str(PROFILE),
        "--env",
        str(ENVIRONMENT),
        "--target",
        "fixture-target",
    )
    if preview.get("schema") != "rexecop.action_preview.v0.1":
        raise SystemExit(f"unexpected action preview schema: {preview}")
    operation_id = _run(
        "--root",
        str(runtime_root),
        "plan",
        "--profile",
        str(PROFILE),
        "--env",
        str(POLICY_ENVIRONMENT),
        "--intent",
        "inspect_fixture_state",
        "--target",
        "fixture-target",
        "--mode",
        "dry_run",
    ).strip()
    if not operation_id.startswith("op-"):
        raise SystemExit(f"unexpected operation id: {operation_id}")
    review = _json(
        "--root",
        str(runtime_root),
        "operation",
        "review",
        "--operation",
        operation_id,
    )
    if review.get("status") != "proceed":
        raise SystemExit(f"operation review blocked: {review}")
    started = _json("--root", str(runtime_root), "start", "--operation", operation_id)
    if started.get("state") != "completed":
        raise SystemExit(f"start did not complete: {started}")
    status = _json(
        "--root",
        str(runtime_root),
        "status",
        "--operation",
        operation_id,
    )
    if status.get("state") != "completed":
        raise SystemExit(f"status not completed: {status}")
    receipt = _json(
        "--root",
        str(runtime_root),
        "receipt",
        "show",
        operation_id,
    )
    if receipt.get("schema") != "rexecop.receipt_show.v0.1":
        raise SystemExit(f"unexpected receipt schema: {receipt}")
    print(f"operator_readonly_journey_ok:operation={operation_id}")


def _run_failure_journey(runtime_root: Path) -> None:
    _run("--root", str(runtime_root), "init")
    blocked = _run_expect_failure(
        "action",
        "policy-preview",
        "apply_fixture_change",
        "--profile",
        str(PROFILE),
        "--env",
        str(POLICY_ENVIRONMENT),
        "--target",
        "fixture-target",
        "--mode",
        "apply",
    )
    if blocked.get("status") != "blocked":
        raise SystemExit(f"expected blocked policy preview: {blocked}")
    operation_id = _run(
        "--root",
        str(runtime_root),
        "plan",
        "--profile",
        str(PROFILE),
        "--env",
        str(POLICY_ENVIRONMENT),
        "--intent",
        "inspect_fixture_state",
        "--target",
        "fixture-target",
        "--mode",
        "dry_run",
    ).strip()
    failed = _run_with_env(
        {"REXECOP_STATIC_FIXTURE_FAILURES": FAILURE_ENV},
        "--root",
        str(runtime_root),
        "start",
        "--operation",
        operation_id,
    )
    if failed.get("state") != "failed":
        raise SystemExit(f"expected failed operation after injected failures: {failed}")
    ops_code = _run_returncode("--root", str(runtime_root), "ops")
    if ops_code != 1:
        raise SystemExit(f"ops expected exit 1 with blockers, got {ops_code}")
    explained = _json(
        "--root",
        str(runtime_root),
        "explain-error",
        operation_id,
    )
    if explained.get("schema") != "rexecop.explain_error.v0.1":
        raise SystemExit(f"unexpected explain-error schema: {explained}")
    runbook = _json(
        "runbook",
        "show",
        "inspect_fixture_state",
        "--profile",
        str(PROFILE),
    )
    if runbook.get("schema") != "rexecop.runbook_show.v0.1":
        raise SystemExit(f"unexpected runbook schema: {runbook}")
    retried = _json(
        "--root",
        str(runtime_root),
        "retry",
        "--operation",
        operation_id,
    )
    if retried.get("state") != "completed":
        raise SystemExit(f"retry did not complete: {retried}")
    recover = _json("--root", str(runtime_root), "runtime", "recover", "--json")
    if recover.get("schema") != "rexecop.runtime_recovery.v0.1":
        raise SystemExit(f"unexpected runtime recover schema: {recover}")
    print(f"operator_failure_journey_ok:operation={operation_id}")


def _run_governance_journey() -> None:
    controls = _json("governance", "controls")
    if controls.get("schema") != "rexecop.governance_controls.v0.1":
        raise SystemExit(f"unexpected governance controls schema: {controls}")
    if controls.get("status") != "passed":
        raise SystemExit(f"governance controls blocked: {controls}")
    profile_controls = _json(
        "governance",
        "controls",
        "--profile",
        str(PROFILE),
        "--track",
        "readonly",
    )
    if profile_controls.get("profile_governance") is None:
        raise SystemExit("profile governance projection missing")
    policy = _json(
        "policy",
        "explain",
        "--profile",
        str(PROFILE),
        "--env",
        str(POLICY_ENVIRONMENT),
        "--intent",
        "inspect_fixture_state",
        "--target",
        "fixture-target",
        "--mode",
        "dry_run",
    )
    if policy.get("status") != "explained":
        raise SystemExit(f"policy explain unexpected status: {policy}")
    catalog_preview = _json(
        "action",
        "policy-preview",
        "inspect",
        "--profile",
        str(FIRST_RUN_PROFILE),
        "--env",
        str(FIRST_RUN_ENVIRONMENT),
        "--catalog",
        str(CATALOG),
        "--target",
        "fixture-target",
        "--mode",
        "dry_run",
    )
    if catalog_preview.get("schema") != "rexecop.action_policy_impact.v0.1":
        raise SystemExit(f"unexpected catalog policy preview: {catalog_preview}")
    print("operator_governance_journey_ok")


def _run_audit_journey(runtime_root: Path) -> None:
    operations = sorted((runtime_root / "operations").glob("op-*.json"))
    if not operations:
        raise SystemExit("audit journey requires a completed readonly operation")
    operation_id = operations[0].stem
    history = _json(
        "--root",
        str(runtime_root),
        "history",
        "--operation",
        operation_id,
    )
    if not history.get("operation_id") or not history.get("transitions"):
        raise SystemExit(f"unexpected history payload: {history}")
    truth_path = _json(
        "--root",
        str(runtime_root),
        "operation",
        "truth-path",
        "--operation",
        operation_id,
    )
    if truth_path.get("schema") != "rexecop.truth_path_projection.v0.1":
        raise SystemExit(f"unexpected truth-path schema: {truth_path}")
    chain = _json(
        "--root",
        str(runtime_root),
        "chain",
        "summary",
        operation_id,
    )
    if chain.get("schema") != "rexecop.chain_summary.v0.1":
        raise SystemExit(f"unexpected chain summary schema: {chain}")
    bundle = _json(
        "--root",
        str(runtime_root),
        "support",
        "bundle",
        operation_id,
        "--redacted",
    )
    if bundle.get("schema") != "rexecop.support_bundle.v0.1":
        raise SystemExit(f"unexpected support bundle schema: {bundle}")
    print(f"operator_audit_journey_ok:operation={operation_id}")


def _json(*args: str) -> dict[str, object]:
    output = _run(*args)
    payload = json.loads(output)
    if not isinstance(payload, dict):
        raise SystemExit(f"expected JSON object from {' '.join(args)}")
    return payload


def _run_expect_failure(*args: str) -> dict[str, object]:
    cmd = [sys.executable, "-m", "rexecop.cli", *args]
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode == 0:
        raise SystemExit(
            "command expected failure: "
            + " ".join(cmd)
            + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    payload = json.loads(result.stdout or result.stderr)
    if not isinstance(payload, dict):
        raise SystemExit(f"expected JSON object from failed {' '.join(args)}")
    return payload


def _run_with_env(
    env: dict[str, str],
    *args: str,
    expect_failure: bool = False,
) -> dict[str, object]:
    cmd = [sys.executable, "-m", "rexecop.cli", *args]
    merged_env = os.environ.copy()
    merged_env.update(env)
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
        env=merged_env,
    )
    if expect_failure and result.returncode == 0:
        raise SystemExit(f"expected failure for {' '.join(cmd)}")
    if not expect_failure and result.returncode != 0:
        raise SystemExit(
            "command failed: "
            + " ".join(cmd)
            + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise SystemExit(f"expected JSON object from {' '.join(args)}")
    return payload


def _run_returncode(*args: str) -> int:
    cmd = [sys.executable, "-m", "rexecop.cli", *args]
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode not in {0, 1}:
        raise SystemExit(
            "unexpected exit code: "
            + " ".join(cmd)
            + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.returncode


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
