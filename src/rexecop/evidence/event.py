from __future__ import annotations

from enum import StrEnum


class EvidenceEventType(StrEnum):
    OPERATION_CREATED = "operation_created"
    PLAN_GENERATED = "plan_generated"
    GOVENGINE_DECISION_REQUESTED = "govengine_decision_requested"
    GOVENGINE_DECISION_RECEIVED = "govengine_decision_received"
    APPROVAL_RECEIVED = "approval_received"
    STATE_TRANSITION = "state_transition"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    VALIDATION_STARTED = "validation_started"
    VALIDATION_COMPLETED = "validation_completed"
    RECEIPT_GENERATED = "receipt_generated"
    OPERATION_COMPLETED = "operation_completed"
    OPERATION_FAILED = "operation_failed"
    OPERATION_ESCALATED = "operation_escalated"
