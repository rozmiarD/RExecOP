from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from rexecop.errors import RExecOpValidationError

AUTOMATION_ADMISSION_SCHEMA = "rexecop.automation_admission_binding.v0.1"
AUTOMATION_CHAIN_SCHEMA_REF = "schemas/automation_chain.v0.1.schema.json"


@dataclass(frozen=True)
class AutomationAdmissionBinding:
    status: str
    reason_code: str
    request: Mapping[str, Any]
    request_digest: str
    admission: Mapping[str, Any]
    admission_digest: str
    explanation: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": AUTOMATION_ADMISSION_SCHEMA,
            "status": self.status,
            "reason_code": self.reason_code,
            "request": dict(self.request),
            "request_digest": self.request_digest,
            "admission": dict(self.admission),
            "admission_digest": self.admission_digest,
            "explanation": dict(self.explanation),
            "non_claims": [
                "RExecOp stores GovEngine automation admission; it does not re-evaluate policy.",
                "This binding does not execute or start child operations.",
                "SCLite automation_chain remains the truth artifact shape.",
            ],
        }


def automation_transition_contract_available() -> bool:
    try:
        _govengine_api()
    except RExecOpValidationError:
        return False
    return True


def admit_automation_transition_request(
    request: Mapping[str, Any],
) -> AutomationAdmissionBinding:
    api = _govengine_api()
    transition = api["AutomationTransitionRequest"].from_mapping(request)
    admission = api["admit_automation_transition"](transition)
    explanation = api["explain_automation_transition"](transition)
    admission_payload = admission.as_dict()
    explanation_payload = explanation.as_dict()
    request_payload = transition.as_dict()
    return AutomationAdmissionBinding(
        status="admitted" if bool(admission_payload.get("allowed")) else "blocked",
        reason_code=str(admission_payload.get("reason_code") or ""),
        request=request_payload,
        request_digest=str(api["automation_transition_request_digest"](transition)),
        admission=admission_payload,
        admission_digest=str(api["automation_transition_admission_digest"](admission)),
        explanation=explanation_payload,
    )


def unavailable_automation_binding(reason_code: str) -> dict[str, Any]:
    return {
        "schema": AUTOMATION_ADMISSION_SCHEMA,
        "status": "unavailable",
        "reason_code": reason_code,
        "request": {},
        "request_digest": "",
        "admission": {},
        "admission_digest": "",
        "explanation": {},
        "non_claims": [
            "GovEngine automation transition contract is not available in this install.",
            "No automation admission digest is claimed for this chain decision.",
        ],
    }


def _govengine_api() -> dict[str, Any]:
    try:
        from govengine import (  # type: ignore[attr-defined]
            AutomationTransitionRequest,
            admit_automation_transition,
            automation_transition_admission_digest,
            automation_transition_request_digest,
            explain_automation_transition,
        )
    except ImportError as exc:
        raise RExecOpValidationError(
            "govengine_automation_transition_contract_unavailable"
        ) from exc
    missing = [
        name
        for name, value in {
            "AutomationTransitionRequest": AutomationTransitionRequest,
            "admit_automation_transition": admit_automation_transition,
            "automation_transition_admission_digest": automation_transition_admission_digest,
            "automation_transition_request_digest": automation_transition_request_digest,
            "explain_automation_transition": explain_automation_transition,
        }.items()
        if value is None
    ]
    if missing:
        raise RExecOpValidationError(
            "govengine_automation_transition_contract_unavailable"
        )
    return {
        "AutomationTransitionRequest": AutomationTransitionRequest,
        "admit_automation_transition": admit_automation_transition,
        "automation_transition_admission_digest": automation_transition_admission_digest,
        "automation_transition_request_digest": automation_transition_request_digest,
        "explain_automation_transition": explain_automation_transition,
    }
