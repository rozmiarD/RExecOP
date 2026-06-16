from __future__ import annotations

from rexecop.adapters.govengine_port.contracts import GovEngineAdapter, GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter

MUTATING_MODES = frozenset({"apply", "recovery"})


def is_mutating_mode(mode: str) -> bool:
    return mode in MUTATING_MODES


def default_govengine_adapter() -> GovEngineAdapter:
    """Fail-closed bootstrap adapter for local development."""
    return StaticGovEngineAdapter(
        GovEngineDecisionType.BLOCKED,
        summary="default static adapter blocks mutating execution",
    )
