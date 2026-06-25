from __future__ import annotations

from rexecop.adapters.govengine_port.contracts import (
    GovEngineDecisionType,
    GovEngineRequest,
)
from rexecop.adapters.govengine_port.static_adapter import (
    STATIC_ADAPTER_NOTICE,
    StaticGovEngineAdapter,
)


def test_static_adapter_returns_configured_decision() -> None:
    adapter = StaticGovEngineAdapter(GovEngineDecisionType.APPROVAL_REQUIRED)
    decision = adapter.evaluate(
        GovEngineRequest(
            operation_id="op-test",
            profile="runtime-fixture",
            environment="env",
            intent="apply_fixture_change",
            target="fixture-target",
            mode="apply",
            risk="low",
        )
    )
    assert decision.decision_type == GovEngineDecisionType.APPROVAL_REQUIRED
    assert decision.details["adapter"] == "static"
    assert decision.details["bootstrap_only"] is True


def test_static_adapter_is_documented_bootstrap_only() -> None:
    assert "bootstrap/test only" in STATIC_ADAPTER_NOTICE
