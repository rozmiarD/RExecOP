from __future__ import annotations

from pathlib import Path

from rexecop.operation.controller import OperationController
from rexecop.operation.plan import OperationPlan
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def test_operation_plan_fields_materialized(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = OperationController(store=store).plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    plan = OperationPlan.from_dict(
        __import__("json").loads((store.plans_dir / f"{operation.id}.json").read_text())
    )

    assert plan.operation_id == operation.id
    assert plan.profile == "runtime_fixture"
    assert plan.environment == "runtime-fixture"
    assert plan.intent == "inspect_fixture_state"
    assert plan.target == "fixture-target"
    assert plan.planned_steps
    assert plan.required_connectors == ["fixture_source"]
    assert plan.govengine_request_preview["note"] == "preview only; not a governance decision"
    assert "produce_receipt" in plan.pause_safe_points
    assert plan.retry_policy_summary["max_attempts"] == 2
    assert plan.rollback_available is False
