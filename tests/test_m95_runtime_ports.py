from __future__ import annotations

import inspect
from pathlib import Path

from rexecop.operation import controller as controller_module
from rexecop.orchestration import orchestrator as orchestrator_module
from rexecop.storage.file_store import FileStore
from rexecop.storage.port import RuntimeStore
from rexecop.storage.sqlite_store import SqliteStore


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
        assert store.release_execution_lease(renewed) is True
