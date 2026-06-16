from __future__ import annotations

from pathlib import Path

from rexecop.operation.controller import OperationController
from rexecop.operation.plan import OperationPlan
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"


def test_operation_plan_fields_materialized(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    operation = OperationController(store=store).plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )
    plan = OperationPlan.from_dict(
        __import__("json").loads((store.plans_dir / f"{operation.id}.json").read_text())
    )

    assert plan.operation_id == operation.id
    assert plan.profile == "tecrax"
    assert plan.environment == "small-public-unit-proxmox"
    assert plan.intent == "check_backup_status"
    assert plan.target == "all_critical_vms"
    assert plan.planned_steps
    assert plan.required_connectors == ["proxmox", "pbs"]
    assert plan.govengine_request_preview["note"] == "preview only; not a governance decision"
    assert "resolve_inventory" in plan.pause_safe_points
    assert plan.retry_policy_summary["max_attempts"] == 2
    assert plan.rollback_available is False
