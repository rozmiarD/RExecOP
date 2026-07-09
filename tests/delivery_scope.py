"""Canonical alpha delivery test scope — single source of truth for sign-off."""

from __future__ import annotations

from pathlib import Path

# Behavioral modules included in `pytest -m delivery` and sign-off script.
DELIVERY_TEST_MODULES: tuple[str, ...] = (
    "test_action_surface",
    "test_alpha_gate",
    "test_apply_gating",
    "test_apply_vertical_slice_e2e",
    "test_connector_backend_plugins",
    "test_connector_policy_engine",
    "test_composite_runtime_routing",
    "test_cross_repo_golden_fixture",
    "test_delivery_coverage",
    "test_execution_receipt_honesty",
    "test_fixture_bundle_isolation",
    "test_http_api_connector",
    "test_http_health_check_e2e",
    "test_internal_action_registry",
    "test_observability",
    "test_operator_journeys",
    "test_phase14_connectors",
    "test_public_truth_consistency",
    "test_readonly_vertical_slice_e2e",
    "test_retry_policy",
    "test_rollback_contract",
    "test_secret_resolver",
    "test_sqlite_store",
    "test_stage_a_contracts",
    "test_typed_execution_governance",
    "test_typed_execution_spec",
    "test_staging_connectors_e2e",
    "test_storage_backends",
    "test_worker_runtime",
    "test_workflow_harness",
)

# Documented themes mapped to delivery modules (values must stay in DELIVERY_TEST_MODULES).
DELIVERY_THEMES: dict[str, str] = {
    "action_surface": "test_action_surface",
    "alpha_gate": "test_alpha_gate",
    "apply_gating": "test_apply_gating",
    "apply_slice": "test_apply_vertical_slice_e2e",
    "connector_backend_plugins": "test_connector_backend_plugins",
    "connector_policy_engine": "test_connector_policy_engine",
    "composite_runtime_routing": "test_composite_runtime_routing",
    "cross_repo_golden_fixture": "test_cross_repo_golden_fixture",
    "delivery_coverage_meta": "test_delivery_coverage",
    "receipt_honesty": "test_execution_receipt_honesty",
    "fixture_isolation": "test_fixture_bundle_isolation",
    "http_api_basics": "test_http_api_connector",
    "golden_http_health": "test_http_health_check_e2e",
    "neutral_core_plugins": "test_internal_action_registry",
    "observability": "test_observability",
    "operator_journeys": "test_operator_journeys",
    "http_api_hardening": "test_phase14_connectors",
    "public_truth": "test_public_truth_consistency",
    "readonly_slice": "test_readonly_vertical_slice_e2e",
    "retry_policy": "test_retry_policy",
    "rollback_contract": "test_rollback_contract",
    "secrets": "test_secret_resolver",
    "stage_a_contracts": "test_stage_a_contracts",
    "typed_execution_governance": "test_typed_execution_governance",
    "typed_execution_spec": "test_typed_execution_spec",
    "sqlite_storage": "test_sqlite_store",
    "staging_connectors": "test_staging_connectors_e2e",
    "storage_backends": "test_storage_backends",
    "worker_runtime": "test_worker_runtime",
    "workflow_harness": "test_workflow_harness",
}

SIGNOFF_SCRIPT_REL = "scripts/run_alpha_signoff_checks.sh"
SIGNOFF_PYTEST_MARKER = "delivery"
SIGNOFF_INNER_ENV = "REXECOP_SIGNOFF_INNER"
SIGNOFF_BUILD_ENV = "REXECOP_SIGNOFF_BUILD"

# Meta gate: validates sign-off assets; must never be in DELIVERY_TEST_MODULES.
SIGNOFF_GATE_MODULE = "test_alpha_signoff_gate"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def delivery_module_paths(root: Path | None = None) -> list[Path]:
    base = root or repo_root()
    return [base / "tests" / f"{name}.py" for name in DELIVERY_TEST_MODULES]
