from __future__ import annotations

import json
from typing import Any

from rexecop.evidence.redaction import redact_payload, redact_text

CLI_ERROR_SCHEMA = "rexecop.cli_error.v0.1"


def cli_error_payload(
    *,
    error_class: str,
    reason_code: str,
    message: str,
    command: tuple[str, ...],
    safe_next_actions: tuple[str, ...] = (),
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": CLI_ERROR_SCHEMA,
        "status": "error",
        "error_class": error_class,
        "reason_code": reason_code,
        "message": redact_text(message),
        "command": " ".join(command),
        "argv": list(command),
        "safe_next_actions": list(safe_next_actions),
        "details": redact_payload(details or {}),
        "non_claims": [
            "Does not expose raw secrets or private connector payloads.",
            "Does not execute remediation.",
            "Details are diagnostic projections only.",
        ],
    }


def cli_error_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def validation_cli_error(
    *,
    command: tuple[str, ...],
    message: str,
    reason_code: str = "validation_error",
    safe_next_actions: tuple[str, ...] = (),
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return cli_error_payload(
        error_class="validation_error",
        reason_code=reason_code,
        message=message,
        command=command,
        safe_next_actions=safe_next_actions,
        details=details,
    )
