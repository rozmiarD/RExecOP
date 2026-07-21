#!/usr/bin/env python3
"""Fail closed on moving GitHub Action references in release workflows."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
USES = re.compile(r"^\s*(?:-\s+)?uses:\s+([^@\s]+)@([^\s#]+)", re.MULTILINE)
REQUIRED_PINS = {
    "actions/checkout": "df4cb1c069e1874edd31b4311f1884172cec0e10",
    "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/attest-build-provenance": (
        "977bb373ede98d70efdf65b84cb5f73e068dcc2a"
    ),
    "pypa/gh-action-pypi-publish": (
        "cef221092ed1bacb1cc03d23a2d87d1d172e277b"
    ),
}


def validate_workflow_security() -> dict[str, int]:
    paths = sorted(WORKFLOWS.glob("*.yml"))
    if not paths:
        raise AssertionError("workflow_security_missing_workflows")
    action_count = 0
    seen: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for action, reference in USES.findall(text):
            action_count += 1
            seen.add(action)
            if not FULL_SHA.fullmatch(reference):
                raise AssertionError(
                    f"workflow_action_not_pinned:{path.name}:{action}@{reference}"
                )
            required = REQUIRED_PINS.get(action)
            if required is not None and reference != required:
                raise AssertionError(
                    f"workflow_action_unreviewed_pin:{path.name}:{action}@{reference}"
                )
    missing = sorted(set(REQUIRED_PINS) - seen)
    if missing:
        raise AssertionError(f"workflow_required_action_missing:{','.join(missing)}")
    publish = (WORKFLOWS / "publish.yml").read_text(encoding="utf-8")
    for marker in (
        "name: pypi",
        "id-token: write",
        "artifact-metadata: write",
        "pypa/gh-action-pypi-publish@",
        "dist/*.cdx.json",
        "steps.release_subject_attestation.outputs.attestation-id",
        "steps.release_subject_attestation.outputs.attestation-url",
        'default: "0.3.0rc3"',
        'default: "2470373c6384c284ab48df7ce763f0938797d155"',
    ):
        if marker not in publish:
            raise AssertionError(f"workflow_publish_missing:{marker}")
    for forbidden in (
        "PYPI_" + "API_TOKEN",
        "TWINE_PASSWORD",
        "twine upload",
        "skip-existing:",
    ):
        if forbidden in publish:
            raise AssertionError(f"workflow_publish_unsafe_setting:{forbidden}")
    repair = (WORKFLOWS / "repair-release-evidence.yml").read_text(encoding="utf-8")
    for marker in (
        "artifact-metadata: write",
        "validate_supply_chain_gate.py dist --version",
        "dist/*.cdx.json",
        "steps.release_subject_attestation.outputs.attestation-id",
        "steps.release_subject_attestation.outputs.attestation-url",
    ):
        if marker not in repair:
            raise AssertionError(f"workflow_repair_missing:{marker}")
    return {"workflows": len(paths), "actions": action_count}


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser().parse_args(argv)
    report = validate_workflow_security()
    print(
        "workflow_security_ok:"
        f"workflows={report['workflows']}:actions={report['actions']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
