from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from govengine.execution.command_shape import normalize_argv


def _contains_no_restricted_patterns(
    tool: Any,
    args: Iterable[Any],
) -> tuple[bool, str]:
    del tool, args
    return False, ""


def normalize_allowlisted_argv(
    *,
    tool: str,
    args: Iterable[Any],
    allowed_tools: Iterable[str],
) -> list[str]:
    """Validate allowlisted shell invocation via GovEngine command_shape."""
    return normalize_argv(
        tool,
        args,
        allowed_tools=allowed_tools,
        contains_tool_restricted_patterns=_contains_no_restricted_patterns,
        normalize_tool=lambda value: str(value).strip().lower(),
    )
