from __future__ import annotations

from rexecop.adapters.sclite_port.contracts import EVENT_SCLITE_MAPPING
from rexecop.evidence.event import EvidenceEventType
from rexecop.evidence.redaction import redact_payload


def test_evidence_redacts_secret_like_fields() -> None:
    payload = {
        "api_key": "secret-value",
        "nested": {"password": "abc", "safe": "ok"},
        "token": "tok",
    }
    redacted = redact_payload(payload)
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert redacted["nested"]["safe"] == "ok"
    assert redacted["token"] == "[REDACTED]"


def test_every_evidence_event_declares_future_sclite_mapping() -> None:
    for event_type in EvidenceEventType:
        mapping = EVENT_SCLITE_MAPPING[event_type.value]
        assert mapping["future_artifact"]
        assert mapping["sclite_schema_ref"].startswith("schemas/")
