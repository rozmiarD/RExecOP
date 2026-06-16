from __future__ import annotations

from pathlib import Path

from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"


def _controller(tmp_path: Path, decision: GovEngineDecisionType) -> OperationController:
    return OperationController(
        store=FileStore(tmp_path / ".rexecop"),
        govengine_adapter=StaticGovEngineAdapter(decision),
    )


def test_apply_blocked_when_adapter_returns_blocked(tmp_path: Path) -> None:
    controller = _controller(tmp_path, GovEngineDecisionType.BLOCKED)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="apply",
    )
    assert operation.state == OperationState.BLOCKED.value
    assert operation.govengine_decision_type == "blocked"
    assert not controller.allows_mutating_execution(operation.id)


def test_apply_waits_when_adapter_returns_approval_required(tmp_path: Path) -> None:
    operation = _controller(tmp_path, GovEngineDecisionType.APPROVAL_REQUIRED).plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="apply",
    )
    assert operation.state == OperationState.WAITING_FOR_APPROVAL.value
    assert operation.govengine_decision_type == "approval_required"


def test_apply_allowed_transitions_to_approved(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="apply",
    )
    assert operation.state == OperationState.APPROVED.value
    assert controller.allows_mutating_execution(operation.id)


def test_dry_run_does_not_call_governance_gate(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(
        store=store,
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.BLOCKED),
    )
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )
    assert operation.state == OperationState.PLANNED.value
    events = store.list_evidence_events(operation.id)
    assert not any(event["event_type"] == "govengine_decision_requested" for event in events)
