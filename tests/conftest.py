from __future__ import annotations

import pytest

from delivery_scope import DELIVERY_TEST_MODULES
from rexecop.evidence.redaction import clear_registered_secret_values

_DELIVERY_STEMS = frozenset(DELIVERY_TEST_MODULES)


@pytest.fixture(autouse=True)
def isolate_registered_secret_values():
    clear_registered_secret_values()


@pytest.fixture
def allow_lab_mutation_runtime_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enable mutation mechanics without claiming stable-runtime readiness."""

    monkeypatch.setenv("REXECOP_MUTATION_POSTURE", "lab_only")


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
