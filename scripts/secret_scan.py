#!/usr/bin/env python3
"""Worktree/history CLI wrapper for the packaged RExecOp secret scanner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from rexecop.security.secret_scan import (  # noqa: E402
    Finding,
    scan_commit_messages,
    scan_data,
    scan_history,
    scan_path,
    scan_worktree,
)

# These findings are reachable immutable Git blobs, not a current-worktree
# exception. Every field is part of the key so a new finding cannot inherit this
# baseline merely by sharing a path, rule, or fingerprint.
IMMUTABLE_HISTORY_BASELINE = frozenset(
    {
        (
            "0a598d14c5186bd6d94eb231ad383f790e66968f",
            "OPERATOR_RUNBOOK.md",
            43,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "12b087f0b804bd15d191011bcd032e792020cfc8",
            "OPERATOR_RUNBOOK.md",
            48,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "2988f14c62d5d143eca619cca9f395280505e908",
            "OPERATOR_RUNBOOK.md",
            48,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "3161ce5d72e01deba07153b8a982e92d70ad6a89",
            "OPERATOR_RUNBOOK.md",
            48,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "54646085464809ad47042a8636539c47fb8a7ce8",
            "OPERATOR_RUNBOOK.md",
            43,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "5680a8df743b6d7acd35afd9db4c8308c60c39de",
            "OPERATOR_RUNBOOK.md",
            48,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "612a5b423383760c672766f4748f9252f7bb27e1",
            "OPERATOR_RUNBOOK.md",
            43,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "6cbbd4c15982574921b6948b0523e5f76791ab90",
            "OPERATOR_RUNBOOK.md",
            48,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "abdc9a4be67d6ef82af0cf79655c8c80d0e1413d",
            "OPERATOR_RUNBOOK.md",
            48,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "b42c5c64c663037e1aa79bac7ce5e970c1c3b6fe",
            "OPERATOR_RUNBOOK.md",
            44,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "c2af65328e18d6564d045012b9061d4105cf2c90",
            "OPERATOR_RUNBOOK.md",
            48,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "d0204fdf31276d695c7eb9002e9e8d8f126e98b5",
            "examples/secrets/staging-http.lab.example.yaml",
            6,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "d09131023104f23f1fb9d5b1929d41470e1a1e56",
            "OPERATOR_RUNBOOK.md",
            48,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "dad3e657069f78b34c246b7136fc9e8761bd72b6",
            "OPERATOR_RUNBOOK.md",
            48,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
        (
            "dcde7baf9fe5736494d1e83bbdee2d9c145d9744",
            "OPERATOR_RUNBOOK.md",
            48,
            "credential_assignment",
            "1d4c203ca55d",
            "history",
        ),
    }
)


def _history_baseline_key(finding: Finding) -> tuple[str, str, int, str, str, str]:
    return (
        finding.identity,
        finding.path,
        finding.line,
        finding.rule,
        finding.fingerprint,
        finding.scope,
    )


def _is_immutable_history_baseline(finding: Finding) -> bool:
    return _history_baseline_key(finding) in IMMUTABLE_HISTORY_BASELINE


def main(*, root: Path, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan RExecOp without printing secret values.")
    parser.add_argument(
        "--history",
        action="store_true",
        help="scan every blob reachable from refs and reflogs",
    )
    args = parser.parse_args(argv)
    findings = scan_worktree(root)
    if args.history:
        findings.extend(scan_history(root))
        findings.extend(scan_commit_messages(root))
    unique = sorted(
        {finding for finding in findings if not _is_immutable_history_baseline(finding)},
        key=lambda item: item.render(),
    )
    if unique:
        for finding in unique:
            print(f"possible_secret:{finding.render()}")
        return 1
    print("secret_scan_ok:no_candidates")
    return 0


__all__ = [
    "Finding",
    "main",
    "scan_commit_messages",
    "scan_data",
    "scan_history",
    "scan_path",
    "scan_worktree",
]


if __name__ == "__main__":
    raise SystemExit(main(root=ROOT))
