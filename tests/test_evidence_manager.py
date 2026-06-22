from __future__ import annotations

from rexecop.adapters.sclite_port.contracts import EVENT_SCLITE_MAPPING
from rexecop.evidence.event import EvidenceEventType
from rexecop.evidence.redaction import REDACTED, redact_payload, redact_text, register_secret_value


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


def test_evidence_redacts_registered_secret_in_neutral_fields() -> None:
    secret = "fixture-runtime-secret-value"
    register_secret_value(secret)
    redacted = redact_payload(
        {
            "stdout": f"result={secret}",
            "nested": {"value": secret},
            "error": f"request failed with {secret}",
        }
    )
    assert secret not in str(redacted)
    assert redacted["nested"]["value"] == REDACTED


def test_redact_text_masks_provider_and_assignment_patterns() -> None:
    text = "{}={} {}: {} {}".format(
        "token",
        "fixture-token-value",
        "Authorization",
        "Bearer",
        "abcdefgh",
    )
    redacted = redact_text(text)
    assert "fixture-token-value" not in redacted
    assert "abcdefgh" not in redacted
    assert REDACTED in redacted


def test_every_evidence_event_declares_future_sclite_mapping() -> None:
    for event_type in EvidenceEventType:
        mapping = EVENT_SCLITE_MAPPING[event_type.value]
        assert mapping["future_artifact"]
        assert mapping["sclite_schema_ref"].startswith("schemas/")
