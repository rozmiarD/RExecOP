from __future__ import annotations

from pathlib import Path

from rexecop.adapters.govengine_port.contracts import GovEngineDecisionType
from rexecop.adapters.govengine_port.static_adapter import StaticGovEngineAdapter
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


def _controller(tmp_path: Path) -> OperationController:
    return OperationController(
        store=FileStore(tmp_path / ".rexecop"),
        govengine_adapter=StaticGovEngineAdapter(GovEngineDecisionType.ALLOWED),
    )


def test_queue_respects_max_concurrent_operations(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    first = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    second = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target-2",
        mode="apply",
    )
    controller.advance(first.id)
    queued = controller.start(second.id)
    assert queued.metadata["queue"]["reason"] == "max_concurrent_reached"
    assert controller.runtime.queue.list_pending() == [second.id]
    queue_file = controller.store.root / "queue" / "run_now.json"
    assert queue_file.stat().st_mode & 0o777 == 0o600
    assert queue_file.parent.stat().st_mode & 0o777 == 0o700


def test_process_queue_starts_next_operation(
    tmp_path: Path,
    allow_mutation_without_governance_for_runtime_test: None,
) -> None:
    controller = _controller(tmp_path)
    first = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    second = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="apply_fixture_change",
        target="fixture-target-2",
        mode="apply",
    )
    controller.advance(first.id)
    controller.start(second.id)
    completed = controller.start(first.id)
    assert completed.state == OperationState.COMPLETED.value
    assert controller.get_operation(second.id).state == OperationState.COMPLETED.value
