from __future__ import annotations

from pathlib import Path

from rexecop.validation.validator import validate_operation_result

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"


def test_validator_hook_for_unknown_intent() -> None:
    from rexecop.profile.loader import load_profile

    profile = load_profile(PROFILE)
    try:
        validate_operation_result(
            intent="unknown_intent",
            shared_state={},
            profile=profile,
        )
    except Exception as exc:
        assert "no validation rules" in str(exc)
    else:
        raise AssertionError("expected validation error")
