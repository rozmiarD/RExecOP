from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from rexecop.errors import RExecOpValidationError
from rexecop.operation import controller as controller_module
from rexecop.operation.model import Operation
from rexecop.orchestration import orchestrator as orchestrator_module
from rexecop.runtime_ops.lease import WorkerLeaseManager
from rexecop.runtime_ops.queue import (
    QUEUE_CLAIM_LIFECYCLE_UNSUPPORTED,
    StoreRunNowQueue,
)
from rexecop.storage.file_store import FileStore
from rexecop.storage.memory_store import InMemoryStore
from rexecop.storage.port import RuntimeStore
from rexecop.storage.sqlite_store import SqliteStore


class _LogicalQueueAdapter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[str] = []

    def _queue_claim_lifecycle(self) -> Any:
        self.calls.append("lifecycle")
        return None

    def _queue_claim_facts(self, _operation_id: str) -> Any:
        self.calls.append("facts")
        return None

    def queue_enqueue(self, _operation_id: str) -> int:
        self.calls.append("enqueue")
        return 0

    def queue_claim(self, _lease: dict[str, Any]) -> dict[str, Any] | None:
        self.calls.append("claim")
        return None


class _UnsupportedMemorySubclass(InMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def queue_enqueue(self, _operation_id: str) -> int:
        self.calls.append("enqueue")
        return 0

    def queue_claim(self, _lease: dict[str, Any]) -> dict[str, Any] | None:
        self.calls.append("claim")
        return None


def test_runtime_store_declares_m95_coordination_ports() -> None:
    required = {
        "save_operation",
        "load_approval",
        "acquire_execution_lease",
        "renew_execution_lease",
        "release_execution_lease",
        "queue_claim",
        "queue_complete_claim",
        "start_execution_attempt",
        "allocate_execution_attempt_id",
        "claim_governance_decision_once",
        "load_execution_permit_for_attempt",
        "finish_execution_attempt",
        "recover_started_attempts",
        "list_pending_projection_operations",
    }

    assert required <= set(dir(RuntimeStore))
    assert {
        "execution_lease_guard",
        "list_execution_attempts",
        "queue_recover_expired_claims",
        "_queue_claim_lifecycle",
        "_queue_claim_facts",
    }.isdisjoint(dir(RuntimeStore))


def test_lifecycle_modules_do_not_read_approval_paths_directly() -> None:
    source = inspect.getsource(controller_module) + inspect.getsource(orchestrator_module)

    assert 'root / "approvals"' not in source
    assert "load_approval" in source


def test_file_and_sqlite_stores_implement_runtime_coordination_ports(
    tmp_path: Path,
) -> None:
    for store in (
        FileStore(tmp_path / "file"),
        SqliteStore(tmp_path / "sqlite"),
    ):
        lease = store.acquire_execution_lease(worker_id="port-test")
        renewed = store.renew_execution_lease(lease)
        assert renewed["lease_epoch"] == lease["lease_epoch"]
        with WorkerLeaseManager(store.root).guard(renewed):
            pass
        assert store.release_execution_lease(renewed) is True


def test_runtime_stores_inventory_attempts_from_their_logical_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_cwd = tmp_path / "memory-cwd"
    memory_cwd.mkdir()
    monkeypatch.chdir(memory_cwd)
    stores = (
        ("file", FileStore(tmp_path / "file-attempts")),
        ("memory", InMemoryStore()),
        ("sqlite", SqliteStore(tmp_path / "sqlite-attempts")),
    )
    for kind, store in stores:
        operation = Operation(
            id=f"op-{kind}",
            profile="fixture",
            environment="fixture",
            intent="inspect",
            target="target",
            mode="dry_run",
            requested_by="test",
            state="approved",
            created_at="2026-07-27T00:00:00+00:00",
            updated_at="2026-07-27T00:00:00+00:00",
        )
        store.save_operation(operation)
        attempt = store.start_execution_attempt(
            operation_id=f"op-{kind}",
            attempt_id=f"attempt-{kind}",
            operation_revision=1,
            step_id="fixture-step",
            plan={"operation_id": f"op-{kind}"},
            execution_spec={"digest": "sha256:" + "a" * 64},
            target="fixture-target",
            mode="apply",
            lease={"lease_epoch": 1, "process_instance_id": "fixture-process"},
        )

        fact_operation, fact_attempts = store._queue_claim_facts(f"op-{kind}")
        assert fact_operation.id == operation.id
        assert fact_attempts == [attempt]
        lease = store.acquire_execution_lease(worker_id=f"guard-{kind}")
        with WorkerLeaseManager(store.root).guard(lease):
            pass
        assert store.release_execution_lease(lease)
        if kind == "memory":
            assert not (store.root / "attempts").exists()


@pytest.mark.parametrize("store_kind", ["logical_adapter", "builtin_subclass"])
@pytest.mark.parametrize("action", ["claim", "enqueue", "recover"])
def test_unsupported_queue_lifecycle_fails_before_any_mutation(
    store_kind: str,
    action: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if store_kind == "logical_adapter":
        store: Any = _LogicalQueueAdapter(tmp_path / "logical-root")
    else:
        memory_cwd = tmp_path / "subclass-cwd"
        memory_cwd.mkdir()
        monkeypatch.chdir(memory_cwd)
        store = _UnsupportedMemorySubclass()
    queue = StoreRunNowQueue(store)
    queue_dir = store.root / "queue"
    assert not queue_dir.exists()

    with pytest.raises(
        RExecOpValidationError,
        match=f"^{QUEUE_CLAIM_LIFECYCLE_UNSUPPORTED}$",
    ) as caught:
        if action == "claim":
            queue.claim_from_lease({})
        elif action == "enqueue":
            queue.enqueue("op-unsupported")
        else:
            queue.recover_expired_claims_from_lease({})

    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    assert store.calls == []
    assert not queue_dir.exists()
