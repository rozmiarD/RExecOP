from __future__ import annotations

from rexecop.adapters.govengine_port.contracts import (
    GovEngineDecision,
    GovEngineDecisionType,
    GovEngineRequest,
)

STATIC_ADAPTER_NOTICE = (
    "StaticGovEngineAdapter is bootstrap/test only and is not production governance."
)


class StaticGovEngineAdapter:
    """Bootstrap/test governance adapter. Not a policy engine."""

    def __init__(
        self,
        decision_type: GovEngineDecisionType,
        *,
        summary: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        self.decision_type = decision_type
        self.summary = summary or f"static decision: {decision_type.value}"
        self.details = dict(details or {})
        self.bootstrap_only = True

    def evaluate(self, request: GovEngineRequest) -> GovEngineDecision:
        return GovEngineDecision(
            decision_type=self.decision_type,
            summary=self.summary,
            details={
                **self.details,
                "adapter": "static",
                "bootstrap_only": True,
                "operation_id": request.operation_id,
            },
        )
