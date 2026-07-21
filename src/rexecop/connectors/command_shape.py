from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import PurePath
from typing import Any

RestrictedPatternCheck = Callable[[Any, Iterable[Any]], tuple[bool, str]]
NormalizeTool = Callable[[Any], str]


def normalize_argv(
    tool: Any,
    args: Iterable[Any],
    *,
    allowed_tools: Iterable[str],
    contains_tool_restricted_patterns: RestrictedPatternCheck,
    normalize_tool: NormalizeTool,
    approved_spec: bool = False,
) -> list[str]:
    """Normalize one runtime-owned tool invocation without executing it."""

    normalized_tool = normalize_tool(tool)
    if not normalized_tool:
        raise ValueError("missing_tool")
    allowed = {
        str(item).strip().lower()
        for item in allowed_tools
        if str(item).strip()
    }
    if normalized_tool not in allowed:
        reason = (
            "tool_not_allowed_for_approved_spec"
            if approved_spec
            else "tool_not_allowed"
        )
        raise ValueError(f"{reason}:{normalized_tool}")
    normalized_args = [str(item) for item in (args or [])]
    if (
        normalized_tool == "curl"
        and "-q" not in normalized_args
        and "--disable" not in normalized_args
    ):
        normalized_args = ["-q", *normalized_args]
    restricted, restricted_pattern = contains_tool_restricted_patterns(
        normalized_tool,
        normalized_args,
    )
    if restricted:
        raise ValueError(
            f"tool_restricted_pattern:{normalized_tool}:{restricted_pattern}"
        )
    return [normalized_tool, *normalized_args]


def _contains_no_restricted_patterns(
    tool: Any,
    args: Iterable[Any],
) -> tuple[bool, str]:
    normalized_tool = PurePath(str(tool).strip().lower()).name
    tokens = [str(item).strip().lower() for item in args]

    if normalized_tool == "sudo" or "sudo" in tokens:
        return True, "sudo"

    if normalized_tool in {"bash", "dash", "sh", "zsh"} and any(
        token in {"-c", "--command"} for token in tokens
    ):
        return True, "shell_command"

    if normalized_tool == "systemctl":
        action = _first_positional(tokens)
        if action in {"start", "stop", "restart", "reload", "enable", "disable"}:
            return True, f"systemctl_{action}"

    if normalized_tool == "service" and any(
        token in {"start", "stop", "restart", "reload"} for token in tokens
    ):
        return True, "service_mutation"

    if normalized_tool == "docker":
        action_index, action = _first_positional_with_index(tokens)
        if action in {"exec", "restart", "start", "stop", "kill", "rm", "run", "update"}:
            return True, f"docker_{action}"
        if action == "compose":
            compose_action = _first_positional(tokens[action_index + 1 :])
            if compose_action in {"up", "down", "restart"}:
                return True, f"docker_compose_{compose_action}"

    if normalized_tool == "docker-compose":
        action = _first_positional(tokens)
        if action in {"up", "down", "restart"}:
            return True, f"docker_compose_{action}"

    return False, ""


def _first_positional(tokens: list[str]) -> str:
    return _first_positional_with_index(tokens)[1]


def _first_positional_with_index(tokens: list[str]) -> tuple[int, str]:
    for index, token in enumerate(tokens):
        if token and not token.startswith("-"):
            return index, token
    return -1, ""


def normalize_allowlisted_argv(
    *,
    tool: str,
    args: Iterable[Any],
    allowed_tools: Iterable[str],
) -> list[str]:
    """Validate an allowlisted shell invocation at the RExecOp I/O boundary."""
    return normalize_argv(
        tool,
        args,
        allowed_tools=allowed_tools,
        contains_tool_restricted_patterns=_contains_no_restricted_patterns,
        normalize_tool=lambda value: str(value).strip().lower(),
    )
