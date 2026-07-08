from __future__ import annotations

import json
from pathlib import Path

import yaml

from rexecop.evidence.manager import EvidenceManager
from rexecop.evidence.public_projection import (
    PUBLIC_PROJECTION_SCHEMA,
    project_public_payload,
    resolve_public_projection_allowlist,
    sanitize_for_public_surface,
)
from rexecop.evidence.redaction import REDACTED
from rexecop.storage.file_store import FileStore


def test_project_public_payload_digests_unallowlisted_fields() -> None:
    payload = {
        "step_id": "collect",
        "success": True,
        "output": {
            "stdout": "sensitive host output",
            "error_class": "timeout",
            "output_digests": {"stdout": "sha256:abc"},
        },
        "notes": "unexpected plaintext",
    }

    projected = project_public_payload(payload)

    assert projected["step_id"] == "collect"
    assert projected["success"] is True
    assert projected["output"]["error_class"] == "timeout"
    assert projected["output"]["stdout"]["projection"] == "digest_only"
    assert projected["output"]["stdout"]["schema"] == PUBLIC_PROJECTION_SCHEMA
    assert projected["notes"]["projection"] == "digest_only"


def test_project_public_payload_honors_declared_allowlist() -> None:
    payload = {
        "output": {
            "stdout": "bounded diagnostic text",
            "stderr": "",
        }
    }

    projected = project_public_payload(
        payload,
        allowlist=frozenset({"output.stdout", "output.stderr"}),
    )

    assert projected["output"]["stdout"] == "bounded diagnostic text"
    assert projected["output"]["stderr"] == ""


def test_sanitize_for_public_surface_applies_redaction_second() -> None:
    sanitized = sanitize_for_public_surface(
        {
            "unexpected_field": "fixture-secret-value",
            "output": {"body_snippet": "token=fixture-secret-value"},
        },
        allowlist=frozenset({"output.body_snippet"}),
    )

    assert sanitized["unexpected_field"]["projection"] == "digest_only"
    assert "fixture-secret-value" not in json.dumps(sanitized)
    assert REDACTED in json.dumps(sanitized)


def test_resolve_public_projection_allowlist_reads_command_shapes(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    (profile / "connectors").mkdir(parents=True)
    (profile / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_contract": {
                    "name": "fixture",
                    "version": "0.1.0",
                    "intents": {"required": True},
                    "workflows": {"required": True},
                    "connector_requirements": {"required": True},
                    "risk_classes": {"required": True},
                    "evidence_requirements": {"required": True},
                    "governance_expectations": {"required": True},
                    "validation_rules": {"required": True},
                    "escalation_rules": {"required": True},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (profile / "connectors" / "host.yaml").write_text(
        yaml.safe_dump(
            {
                "connector": {
                    "name": "host",
                    "backend": "local_shell_readonly",
                    "capabilities": ["uptime"],
                    "command_shapes": {
                        "uptime": {
                            "command": "uptime",
                            "args": ["-p"],
                            "public_projection": {
                                "safe_fields": ["output.stdout"],
                            },
                        }
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    allowlist = resolve_public_projection_allowlist(
        profile=profile,
        connector="host",
        action="uptime",
    )

    assert allowlist == frozenset({"output.stdout"})


def test_evidence_emit_projects_step_payload_before_persist(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runtime")
    manager = EvidenceManager(store)

    manager.emit(
        operation_id="op-1",
        event_type="step_completed",
        payload={
            "step_id": "collect",
            "success": True,
            "output": {"stdout": "host status text", "error_class": "timeout"},
        },
        public_projection_allowlist=frozenset({"output.stdout"}),
    )

    events = store.list_evidence_events("op-1")
    assert len(events) == 1
    payload = events[0]["sanitized_payload"]
    assert payload["output"]["stdout"] == "host status text"
    assert payload["output"]["error_class"] == "timeout"
    assert "host status text" in json.dumps(payload)


def test_evidence_emit_without_allowlist_keeps_redaction_only(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runtime")
    manager = EvidenceManager(store)

    manager.emit(
        operation_id="op-2",
        event_type="watchdog_decision",
        payload={
            "record_id": "wd-1",
            "decision": "block_autostart",
            "operation_id": "op-2",
        },
    )

    payload = store.list_evidence_events("op-2")[0]["sanitized_payload"]
    assert payload["record_id"] == "wd-1"
    assert payload["decision"] == "block_autostart"


def test_evidence_emit_with_empty_allowlist_digests_raw_output(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runtime")
    manager = EvidenceManager(store)

    manager.emit(
        operation_id="op-3",
        event_type="step_completed",
        payload={
            "step_id": "collect",
            "success": True,
            "output": {"stdout": "host status text"},
        },
        public_projection_allowlist=frozenset(),
    )

    payload = store.list_evidence_events("op-3")[0]["sanitized_payload"]
    assert payload["output"]["stdout"]["projection"] == "digest_only"
    assert "host status text" not in json.dumps(payload)
