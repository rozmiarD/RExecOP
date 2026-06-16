from __future__ import annotations

from rexecop.validation.validator import validate_operation_result


def test_validator_hook_for_unknown_intent() -> None:
    try:
        validate_operation_result(intent="unknown_intent", shared_state={})
    except Exception as exc:
        assert "no validation rules" in str(exc)
    else:
        raise AssertionError("expected validation error")
