from __future__ import annotations

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
