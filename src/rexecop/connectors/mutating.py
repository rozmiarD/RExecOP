from __future__ import annotations

MUTATING_ACTIONS = frozenset(
    {
        "restart",
        "delete",
        "create",
        "update",
        "apply",
        "stop",
        "start",
    }
)
