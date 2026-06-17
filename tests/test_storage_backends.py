from __future__ import annotations

from pathlib import Path

import pytest

from rexecop.operation.controller import OperationController
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.operation.state import OperationState
from rexecop.storage.factory import create_store
from rexecop.storage.port import RuntimeStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"


@pytest.fixture(params=["file", "sqlite"])
def runtime_store(tmp_path: Path, request: pytest.FixtureRequest) -> RuntimeStore:
    return create_store(tmp_path / ".rexecop", backend=str(request.param))


def test_storage_round_trip(runtime_store: RuntimeStore) -> None:
    operation = Operation(
        id="op-1",
        profile="http-health-fixture",
        environment="local",
        intent="http_health_check",
        target="api_primary",
        mode="dry_run",
        state=OperationState.PLANNED.value,
        requested_by="test",
        created_at="2026-06-17T00:00:00+00:00",
        updated_at="2026-06-17T00:00:00+00:00",
    )
    plan = OperationPlan(
        operation_id="op-1",
        profile="http-health-fixture",
        environment="local",
        intent="http_health_check",
        target="api_primary",
        mode="dry_run",
        workflow={"id": "http.health_check"},
        planned_steps=[{"id": "probe", "type": "connector"}],
        required_connectors=["health"],
        risk="low",
        govengine_request_preview={},
        expected_evidence=[],
        pause_safe_points=[],
        retry_policy_summary={},
        rollback_available=False,
    )
    runtime_store.save_operation(operation)
    runtime_store.save_plan(plan)
    runtime_store.save_evidence_event("op-1", {"event_id": "ev-1", "event_type": "planned"})

    loaded = runtime_store.load_operation("op-1")
    assert loaded.id == "op-1"
    assert runtime_store.load_plan("op-1").intent == "http_health_check"
    assert len(runtime_store.list_evidence_events("op-1")) == 1
    assert len(runtime_store.list_operations()) == 1


def test_controller_plan_persists_across_backends(runtime_store: RuntimeStore) -> None:
    controller = OperationController(store=runtime_store)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )

    reloaded = runtime_store.load_operation(operation.id)
    assert reloaded.state == OperationState.PLANNED.value
    assert runtime_store.load_plan(operation.id).planned_steps
    events = runtime_store.list_evidence_events(operation.id)
    event_types = [event["event_type"] for event in events]
    assert "operation_created" in event_types
    assert "plan_generated" in event_types


def test_sclite_dir_stays_on_disk_for_sqlite(runtime_store: RuntimeStore) -> None:
    path = runtime_store.operation_sclite_dir("op-sclite")
    assert path.is_dir()
    assert path.parent.name == "sclite"
    assert runtime_store.root.name == ".rexecop"
