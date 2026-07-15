from __future__ import annotations

import pytest

from delivery_scope import DELIVERY_TEST_MODULES
from rexecop.evidence.redaction import clear_registered_secret_values
from rexecop.execution import executor as executor_module

_DELIVERY_STEMS = frozenset(DELIVERY_TEST_MODULES)


@pytest.fixture(autouse=True)
def isolate_registered_secret_values():
    clear_registered_secret_values()


@pytest.fixture
def allow_mutation_without_governance_for_runtime_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep runtime-mechanics tests independent from the still-blocked G2 approval path."""

    def allow_for_test(*, spec, shared_state, **_):  # type: ignore[no-untyped-def]
        step_id = str(spec.get("step_id") or "")
        digest = "sha256:" + "0" * 64
        admission = {
            "allowed": True,
            "outcome": "allowed",
            "reason_code": "test_only_governance_stub",
            "blockers": [],
            "request_id": f"test-only:{step_id}",
            "subject_ref": digest,
            "signal": {},
            "admission_digest": digest,
            "request_digest": digest,
        }
        shared_state.setdefault("typed_execution_admissions", {})[step_id] = admission
        return admission

    monkeypatch.setattr(
        executor_module,
        "enforce_typed_execution_governance",
        allow_for_test,
    )
    yield
    clear_registered_secret_values()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "delivery: alpha delivery-scope behavioral tests (sign-off subset)",
    )
    config.addinivalue_line(
        "markers",
        "signoff_script: invokes run_alpha_signoff_checks.sh (excluded from nested sign-off)",
    )
    config.addinivalue_line(
        "markers",
        "package_smoke: wheel build and twine metadata check",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.stem in _DELIVERY_STEMS:
            item.add_marker(pytest.mark.delivery)
