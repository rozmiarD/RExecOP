from __future__ import annotations

from rexecop.adapters.govengine_port.client import GovEngineClient
from rexecop.adapters.govengine_port.contracts import GovEngineAdapter, GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter


def default_govengine_adapter() -> GovEngineAdapter:
    """Fail-closed real GovEngine client for local development."""
    return GovEngineClient()


def static_govengine_adapter(
    decision_type: GovEngineDecisionType,
    *,
    summary: str = "",
) -> GovEngineAdapter:
    """Explicit bootstrap/test adapter."""
    return StaticGovEngineAdapter(decision_type, summary=summary)
