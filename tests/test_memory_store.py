from __future__ import annotations

from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.operation.state import OperationState
from rexecop.storage.memory_store import InMemoryStore


def test_memory_store_round_trip() -> None:
    store = InMemoryStore()
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
    store.save_operation(operation)
    store.save_plan(plan)
    store.save_evidence_event("op-1", {"event_id": "e1", "type": "planned"})

    loaded = store.load_operation("op-1")
    assert loaded.id == "op-1"
    assert store.load_plan("op-1").intent == "http_health_check"
    assert len(store.list_evidence_events("op-1")) == 1
    assert len(store.list_operations()) == 1
