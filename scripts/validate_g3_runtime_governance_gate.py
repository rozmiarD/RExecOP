#!/usr/bin/env python3
"""Run the G3 runtime signed-decision and atomic-claim gate."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "tests/test_g3_runtime_governance.py",
    "tests/test_typed_execution_governance.py",
    "tests/test_m95_execution_permit.py",
    "tests/test_m9_attempts.py",
    "tests/test_m95_runtime_ports.py",
    "tests/test_trigger_events.py::test_trigger_event_plans_operation_and_records_decision_evidence",
    "tests/test_watchdog_runtime.py::test_watchdog_records_worker_heartbeat",
    "tests/test_reaction_interpreter.py::test_automation_adapter_is_planning_only_and_carries_no_execution_authority",
)


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *TESTS],
        cwd=ROOT,
        env={**os.environ, "REXECOP_SIGNOFF_INNER": "1"},
        check=False,
    )
    if result.returncode:
        return result.returncode
    print(
        "g3_runtime_governance_gate_ok:attempt_preallocation=OK:"
        "trusted_decision=OK:runtime_binding=OK:decision_claim_port=OK:"
        "atomic_claim=OK:"
        "runtime_attempt_permit=OK:pre_io_attempt_journal=OK:"
        "receipt_conformance=OK:sclite_receipt_projection=OK:"
        "legacy_fallbacks_removed=OK:post_io_output_digest=OK:"
        "planning_adapters=OK"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
