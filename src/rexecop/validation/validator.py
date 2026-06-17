from __future__ import annotations

from pathlib import Path
from typing import Any

from rexecop.errors import RExecOpValidationError
from rexecop.profile.loader import LoadedProfile
from rexecop.profile.validation_rules import load_validation_rule_spec


def validate_operation_result(
    *,
    intent: str,
    shared_state: dict[str, Any],
    profile: LoadedProfile | None = None,
    profile_root: Path | None = None,
) -> dict[str, Any]:
    """Evaluate profile-declared validation rules against workflow shared state."""
    root = profile.root if profile is not None else profile_root
    if root is None:
        raise RExecOpValidationError(
            f"profile root required to validate intent: {intent}"
        )

    spec = load_validation_rule_spec(root, intent)
    for step in spec["steps"]:
        if not isinstance(step, dict):
            raise RExecOpValidationError(f"invalid validation step for intent: {intent}")
        result = _evaluate_step(step, shared_state)
        if result is not None:
            return result

    raise RExecOpValidationError(f"validation rules produced no result for intent: {intent}")


def _evaluate_step(step: dict[str, Any], shared_state: dict[str, Any]) -> dict[str, Any] | None:
    step_type = str(step.get("type") or "").strip()
    if step_type == "require_mapping":
        key = str(step.get("key") or "").strip()
        value = shared_state.get(key)
        if not isinstance(value, dict):
            fail = step.get("fail")
            if isinstance(fail, dict):
                return {
                    "passed": False,
                    "rule": str(fail.get("rule") or f"{key}_required"),
                    "details": _resolve_details(fail.get("details"), shared_state),
                }
            return {
                "passed": False,
                "rule": f"{key}_required",
                "details": {"reason": f"missing mapping: {key}"},
            }
        return None

    if step_type == "require_truthy_path":
        path = str(step.get("path") or "").strip()
        if not _path_truthy(shared_state, path):
            return {
                "passed": False,
                "rule": str(step.get("fail_rule") or path),
                "details": _details_from_step(step, shared_state),
            }
        return {
            "passed": True,
            "rule": str(step.get("pass_rule") or path),
            "details": _details_from_step(step, shared_state),
        }

    if step_type == "require_equals":
        path = str(step.get("path") or "").strip()
        expected = step.get("value")
        actual = _get_path(shared_state, path)
        if actual != expected:
            return {
                "passed": False,
                "rule": str(step.get("fail_rule") or f"{path}_equals"),
                "details": _details_from_step(step, shared_state),
            }
        return {
            "passed": True,
            "rule": str(step.get("pass_rule") or f"{path}_equals"),
            "details": _details_from_step(step, shared_state),
        }

    raise RExecOpValidationError(f"unsupported validation step type: {step_type}")


def _details_from_step(step: dict[str, Any], shared_state: dict[str, Any]) -> dict[str, Any]:
    details_from = step.get("details_from")
    if isinstance(details_from, str) and details_from:
        value = shared_state.get(details_from)
        return value if isinstance(value, dict) else {details_from: value}
    details = step.get("details")
    if isinstance(details, dict):
        return _resolve_details(details, shared_state)
    return {}


def _resolve_details(value: Any, shared_state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    resolved: dict[str, Any] = {}
    for key, item in value.items():
        resolved[key] = _resolve_ref(item, shared_state)
    return resolved


def _resolve_ref(value: Any, shared_state: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return _get_path(shared_state, value[1:])
    if isinstance(value, dict):
        return _resolve_details(value, shared_state)
    return value


def _path_truthy(shared_state: dict[str, Any], path: str) -> bool:
    value = _get_path(shared_state, path)
    return bool(value)


def _get_path(shared_state: dict[str, Any], path: str) -> Any:
    current: Any = shared_state
    for part in path.split("."):
        if not part:
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current
