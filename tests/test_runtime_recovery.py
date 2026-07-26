from __future__ import annotations

import hashlib
import io
import json
import multiprocessing
import os
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import rexecop.runtime_ops.backup as backup_module
from rexecop import __version__
from rexecop.cli import app
from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.operation.model import Operation
from rexecop.operation.plan import OperationPlan
from rexecop.operation.state import OperationState
from rexecop.runtime.init import INIT_SCHEMA, RUNTIME_MANIFEST, initialize_runtime_root
from rexecop.runtime_ops.backup import create_runtime_backup, restore_runtime_backup
from rexecop.runtime_ops.idempotency import start_idempotency_key
from rexecop.runtime_ops.lease import WorkerLeaseManager
from rexecop.runtime_ops.reconstruction import collect_runtime_reconstruction_status
from rexecop.runtime_ops.recovery import run_startup_recovery, start_is_idempotent
from rexecop.runtime_ops.target_lock import TargetLockManager
from rexecop.runtime_ops.worker import run_worker
from rexecop.storage.factory import create_store
from rexecop.storage.file_store import FileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture/profile.yaml"
ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"
NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)
runner = CliRunner()


def _try_acquire_lease(root: str, worker_id: str, ready: Any) -> None:
    try:
        WorkerLeaseManager(Path(root)).acquire(worker_id=worker_id, now=NOW)
    except RExecOpValidationError:
        ready.put("conflict")
    else:
        ready.put("acquired")


def _controller(tmp_path: Path) -> OperationController:
    return OperationController(store=FileStore(tmp_path / ".rexecop"))


def _minimal_plan(operation_id: str) -> OperationPlan:
    return OperationPlan(
        operation_id=operation_id,
        profile="runtime-fixture",
        environment="runtime-fixture",
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
        workflow={"id": "fixture-workflow", "steps": []},
        planned_steps=[],
        required_connectors=[],
        risk="low",
        govengine_request_preview={},
        expected_evidence=[],
        pause_safe_points=[],
        retry_policy_summary={"max_attempts": 0},
        rollback_available=False,
    )


def _runtime_manifest_bytes(*, version: str = __version__) -> bytes:
    return (
        json.dumps(
            {
                "schema": INIT_SCHEMA,
                "rexecop_version": version,
                "storage_backend": "file",
            }
        )
        + "\n"
    ).encode()


def _backup_entry(path: str, data: bytes) -> dict[str, str]:
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest()}


def _write_backup_fixture(
    tmp_path: Path,
    *,
    members: list[tuple[str, bytes, str]],
    manifest_files: list[dict[str, str]],
    file_count: int | None = None,
    global_pax_headers: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    archive = tmp_path / "bundle.tar"
    archive_format = tarfile.PAX_FORMAT if global_pax_headers else tarfile.DEFAULT_FORMAT
    with tarfile.open(
        archive,
        "w",
        format=archive_format,
        pax_headers=global_pax_headers,
    ) as handle:
        for name, data, member_type in members:
            member = tarfile.TarInfo(name)
            if member_type == "file":
                member.size = len(data)
                handle.addfile(member, io.BytesIO(data))
            elif member_type == "pax":
                member.size = len(data)
                member.pax_headers = {"comment": "untrusted-extension"}
                handle.addfile(member, io.BytesIO(data))
            elif member_type == "contiguous":
                member.type = tarfile.CONTTYPE
                member.size = len(data)
                handle.addfile(member, io.BytesIO(data))
            elif member_type == "sparse":
                member.type = tarfile.GNUTYPE_SPARSE
                member.size = len(data)
                handle.addfile(member, io.BytesIO(data))
            elif member_type == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = "runtime_manifest.json"
                handle.addfile(member)
            else:
                raise AssertionError(f"unsupported test member type: {member_type}")
    manifest = archive.with_suffix(".manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "schema": "rexecop.runtime_backup.v0.1",
                "rexecop_version": __version__,
                "runtime_root_fingerprint": "0" * 16,
                "created_at": NOW.isoformat(),
                "file_count": len(manifest_files) if file_count is None else file_count,
                "files": manifest_files,
                "archive": archive.name,
            }
        ),
        encoding="utf-8",
    )
    return archive, manifest


def test_plan_attaches_explicit_idempotency_keys(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    keys = operation.metadata.get("idempotency")
    assert isinstance(keys, dict)
    assert keys["schema"] == "rexecop.idempotency.v0.1"
    assert len(str(keys["plan_key"])) == 64
    assert keys["start_key"] == start_idempotency_key(operation.id)
    assert keys["start_key"] != keys["plan_key"]


def test_start_is_idempotent_for_terminal_operation(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    completed = controller.start(operation.id)
    assert completed.state == OperationState.COMPLETED.value
    again = controller.start(operation.id)
    assert again.state == OperationState.COMPLETED.value
    assert start_is_idempotent(again) is True


def test_startup_recovery_interrupts_running_operation(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    now = NOW.isoformat()
    operation = Operation(
        id="op-running-1",
        profile="runtime-fixture",
        environment="runtime-fixture",
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
        requested_by="operator",
        state=OperationState.RUNNING.value,
        created_at=now,
        updated_at=now,
        correlation_id="corr-running-1",
        metadata={"execution_cursor": {"next_step_index": 1}},
    )
    controller.store.save_operation(operation)
    controller.store.save_plan(_minimal_plan(operation.id))

    report = run_startup_recovery(controller.store, controller=controller, now=NOW)

    updated = controller.get_operation(operation.id)
    assert updated.state == OperationState.FAILED.value
    assert updated.metadata["recovery"]["reason"] == "interrupted_by_restart"
    assert report["summary"]["interrupted_count"] == 1


def test_startup_recovery_releases_stale_lock(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    now = NOW.isoformat()
    completed = Operation(
        id="op-done-1",
        profile="runtime-fixture",
        environment="env-a",
        intent="inspect_fixture_state",
        target="target-a",
        mode="apply",
        requested_by="operator",
        state=OperationState.COMPLETED.value,
        created_at=now,
        updated_at=now,
        correlation_id="corr-done-1",
    )
    controller.store.save_operation(completed)
    TargetLockManager(controller.store).acquire(
        environment="env-a",
        target="target-a",
        operation_id="op-done-1",
    )

    report = run_startup_recovery(controller.store, controller=controller, now=NOW)

    assert list((controller.store.root / "locks").glob("*.lock")) == []
    assert report["actions"]["released_stale_locks"][0]["operation_id"] == "op-done-1"


def test_worker_lease_rejects_conflicting_holder(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    lease = WorkerLeaseManager(store.root)
    lease.acquire(worker_id="worker-a", now=NOW)
    with pytest.raises(RExecOpValidationError, match="worker lease held"):
        lease.acquire(worker_id="worker-b", now=NOW + timedelta(seconds=5))


def test_worker_lease_clears_when_stale(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    lease = WorkerLeaseManager(store.root)
    lease.acquire(worker_id="worker-a", now=NOW - timedelta(seconds=300))
    assert lease.clear_if_stale(now=NOW) is True
    renewed = lease.acquire(worker_id="worker-b", now=NOW)
    assert renewed["worker_id"] == "worker-b"
    assert renewed["lease_epoch"] == 2


def test_worker_lease_is_atomic_across_processes(tmp_path: Path) -> None:
    root = tmp_path / ".rexecop"
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    processes = [
        context.Process(target=_try_acquire_lease, args=(str(root), worker, results))
        for worker in ("worker-a", "worker-b")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert sorted(results.get(timeout=2) for _ in processes) == ["acquired", "conflict"]


def test_stale_owner_cannot_release_new_lease_epoch(tmp_path: Path) -> None:
    lease = WorkerLeaseManager(tmp_path / ".rexecop")
    old = lease.acquire(worker_id="worker-a", now=NOW - timedelta(seconds=300))
    current = lease.acquire(worker_id="worker-b", now=NOW)

    with pytest.raises(RExecOpValidationError, match="ownership conflict"):
        lease.release(
            owner_token=str(old["owner_token"]),
            lease_epoch=int(old["lease_epoch"]),
            process_instance_id=str(old["process_instance_id"]),
        )
    assert lease.read() == current


def test_backup_restore_round_trip(tmp_path: Path) -> None:
    source_root = tmp_path / "source" / ".rexecop"
    initialize_runtime_root(source_root)
    controller = OperationController(store=FileStore(source_root))
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    controller.start(operation.id)

    archive_dir = tmp_path / "backup"
    created = create_runtime_backup(source_root, output=archive_dir, now=NOW)
    archive = Path(created["archive"])
    assert archive.is_file()

    target_root = tmp_path / "restored" / ".rexecop"
    restored = restore_runtime_backup(archive=archive, target_root=target_root)
    assert restored["status"] == "restored"
    restored_store = FileStore(target_root)
    restored_ops = restored_store.list_operations()
    assert len(restored_ops) == 1
    assert restored_ops[0].id == operation.id


@pytest.mark.parametrize(
    ("runtime_manifest", "error"),
    [
        (None, "source runtime manifest is required"),
        (b"not-json", "backup runtime manifest is invalid"),
        (b"[]", "backup runtime manifest is invalid"),
        (
            _runtime_manifest_bytes(version="0.2.24a0"),
            "runtime_root_new_root_required",
        ),
    ],
)
def test_backup_create_requires_valid_compatible_source_runtime_manifest_before_output(
    tmp_path: Path,
    runtime_manifest: bytes | None,
    error: str,
) -> None:
    root = tmp_path / "runtime"
    (root / "operations").mkdir(parents=True)
    (root / "operations" / "record.json").write_text("{}\n", encoding="utf-8")
    if runtime_manifest is not None:
        (root / RUNTIME_MANIFEST).write_bytes(runtime_manifest)
    output = tmp_path / "backup.tar"

    with pytest.raises(RExecOpValidationError, match=error):
        create_runtime_backup(root, output=output)

    assert not output.exists()
    assert not output.with_suffix(".manifest.json").exists()


def test_backup_create_rejects_oversized_source_runtime_manifest_before_output(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / RUNTIME_MANIFEST).write_bytes(b" " * (1024 * 1024 + 1))
    output = tmp_path / "backup.tar"

    with pytest.raises(RExecOpValidationError, match="exceeds size limit"):
        create_runtime_backup(root, output=output)

    assert not output.exists()


def test_backup_create_rejects_runtime_root_symlink_before_output(tmp_path: Path) -> None:
    real_root = tmp_path / "real-runtime"
    initialize_runtime_root(real_root)
    linked_root = tmp_path / "linked-runtime"
    linked_root.symlink_to(real_root, target_is_directory=True)
    output = tmp_path / "backup.tar"

    with pytest.raises(RExecOpValidationError, match="ancestors must be real directories"):
        create_runtime_backup(linked_root, output=output)

    assert not output.exists()


def test_backup_create_rejects_source_runtime_manifest_symlink_before_output(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    external_manifest = tmp_path / "external-runtime-manifest.json"
    external_manifest.write_bytes(_runtime_manifest_bytes())
    (root / RUNTIME_MANIFEST).unlink()
    (root / RUNTIME_MANIFEST).symlink_to(external_manifest)
    output = tmp_path / "backup.tar"

    with pytest.raises(RExecOpValidationError, match="symbolic links in selected paths"):
        create_runtime_backup(root, output=output)

    assert not output.exists()


def test_backup_create_rejects_source_runtime_manifest_directory_before_output(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / RUNTIME_MANIFEST).mkdir()
    output = tmp_path / "backup.tar"

    with pytest.raises(RExecOpValidationError, match="real regular file"):
        create_runtime_backup(root, output=output)

    assert not output.exists()


@pytest.mark.parametrize("symlink_kind", ["selected-directory", "file", "directory", "broken"])
def test_backup_create_rejects_symlinks_in_selected_tree_before_output(
    tmp_path: Path,
    symlink_kind: str,
) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    external = tmp_path / "external"
    external.mkdir()
    (external / "outside.json").write_text('{"outside": true}\n', encoding="utf-8")
    operations = root / "operations"
    if symlink_kind == "selected-directory":
        operations.rmdir()
        operations.symlink_to(external, target_is_directory=True)
    elif symlink_kind == "file":
        (operations / "linked.json").symlink_to(external / "outside.json")
    elif symlink_kind == "directory":
        (operations / "linked-directory").symlink_to(
            external,
            target_is_directory=True,
        )
    else:
        (operations / "broken.json").symlink_to(tmp_path / "missing.json")
    output = tmp_path / "backup.tar"

    with pytest.raises(RExecOpValidationError, match="symbolic links"):
        create_runtime_backup(root, output=output)

    assert not output.exists()


def test_backup_create_rejects_symlinked_runtime_root_ancestor(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_root = real_parent / "runtime"
    initialize_runtime_root(real_root)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    output = tmp_path / "backup.tar"

    with pytest.raises(RExecOpValidationError, match="ancestors must be real directories"):
        create_runtime_backup(linked_parent / "runtime", output=output)

    assert not output.exists()


def test_backup_create_root_swap_stays_anchored_to_open_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    original_record = root / "operations" / "original.json"
    original_record.write_bytes(b"original\n")
    outside = tmp_path / "outside"
    initialize_runtime_root(outside)
    (outside / "operations" / "outside.json").write_bytes(b"outside\n")
    moved_root = tmp_path / "moved-runtime"
    original_open = backup_module._open_runtime_root

    def open_and_swap(path: Path) -> int:
        descriptor = original_open(path)
        root.rename(moved_root)
        root.symlink_to(outside, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(backup_module, "_open_runtime_root", open_and_swap)
    created = create_runtime_backup(root, output=tmp_path / "backup.tar", now=NOW)
    sidecar = json.loads(Path(created["manifest"]).read_text(encoding="utf-8"))
    archived_names = {item["path"] for item in sidecar["files"]}

    assert "operations/original.json" in archived_names
    assert "operations/outside.json" not in archived_names


def test_backup_create_archives_immutable_snapshot_when_source_changes_after_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    record = root / "operations" / "record.json"
    record.write_bytes(b"before\n")
    original_copy = backup_module._copy_regular_file
    mutated = False

    def copy_then_mutate(*args: Any, **kwargs: Any):
        nonlocal mutated
        snapshot = original_copy(*args, **kwargs)
        if kwargs["name"] == "operations/record.json" and not mutated:
            record.write_bytes(b"after!\n")
            mutated = True
        return snapshot

    monkeypatch.setattr(backup_module, "_copy_regular_file", copy_then_mutate)
    created = create_runtime_backup(root, output=tmp_path / "backup.tar", now=NOW)
    restored_root = tmp_path / "restored"
    restore_runtime_backup(
        archive=Path(created["archive"]),
        manifest=Path(created["manifest"]),
        target_root=restored_root,
    )

    assert record.read_bytes() == b"after!\n"
    assert (restored_root / "operations" / "record.json").read_bytes() == b"before\n"


def test_backup_create_ignores_unselected_symlink(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    external = tmp_path / "external.txt"
    external.write_text("outside\n", encoding="utf-8")
    (root / "unselected-link").symlink_to(external)

    created = create_runtime_backup(root, output=tmp_path / "backup.tar", now=NOW)
    sidecar = json.loads(Path(created["manifest"]).read_text(encoding="utf-8"))

    assert "unselected-link" not in {item["path"] for item in sidecar["files"]}


def test_backup_create_rejects_destinations_inside_source(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    output = root / "operations" / "bundle.tar"

    with pytest.raises(RExecOpValidationError, match="outside the runtime root"):
        create_runtime_backup(root, output=output, now=NOW)

    assert not output.exists()
    assert not output.with_suffix(".manifest.json").exists()


def test_backup_create_emits_strict_ustar_regular_members(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    created = create_runtime_backup(root, output=tmp_path / "backup.tar", now=NOW)

    with tarfile.open(created["archive"], "r:") as archive:
        members = list(archive)

    assert members
    assert all(member.type == tarfile.REGTYPE for member in members)
    assert all(not member.pax_headers for member in members)
    assert all(member.sparse is None for member in members)
    assert all(member.offset_data == member.offset + tarfile.BLOCKSIZE for member in members)


def test_backup_create_cleans_temporary_outputs_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    output = tmp_path / "output" / "backup.tar"

    def fail_archive(_path: Path, _records: list[Any]) -> None:
        raise OSError("injected archive failure")

    monkeypatch.setattr(backup_module, "_write_strict_archive", fail_archive)
    with pytest.raises(RExecOpValidationError, match="runtime backup creation failed"):
        create_runtime_backup(root, output=output, now=NOW)

    assert not output.exists()
    assert not output.with_suffix(".manifest.json").exists()
    assert not output.parent.exists()


def test_backup_create_rejects_symlinked_output_ancestor(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RExecOpValidationError, match="output parent"):
        create_runtime_backup(root, output=linked / "backup.tar", now=NOW)

    assert list(outside.iterdir()) == []


def test_backup_create_rejects_output_ancestor_swap_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    output_parent = tmp_path / "output"
    output_parent.mkdir()
    moved_parent = tmp_path / "moved-output"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_revalidate = backup_module._revalidate_directory_chain

    def swap_then_revalidate(*args: Any, **kwargs: Any) -> None:
        output_parent.rename(moved_parent)
        output_parent.symlink_to(outside, target_is_directory=True)
        original_revalidate(*args, **kwargs)

    monkeypatch.setattr(
        backup_module,
        "_revalidate_directory_chain",
        swap_then_revalidate,
    )

    with pytest.raises(RExecOpValidationError, match="output parent changed"):
        create_runtime_backup(root, output=output_parent / "backup.tar", now=NOW)

    assert list(outside.iterdir()) == []
    assert list(moved_parent.iterdir()) == []


def test_backup_create_rolls_back_manifest_when_archive_publication_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    output = tmp_path / "new" / "nested" / "backup.tar"
    original_link = os.link
    publication = 0

    def fail_second_link(*args: Any, **kwargs: Any) -> None:
        nonlocal publication
        publication += 1
        if publication == 2:
            raise OSError("injected archive publication failure")
        original_link(*args, **kwargs)

    monkeypatch.setattr(os, "link", fail_second_link)

    with pytest.raises(RExecOpValidationError, match="runtime backup creation failed"):
        create_runtime_backup(root, output=output, now=NOW)

    assert not output.exists()
    assert not output.with_suffix(".manifest.json").exists()
    assert not (tmp_path / "new").exists()


def test_backup_create_chmod_failure_cleans_new_output_parents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    output = tmp_path / "new" / "nested" / "backup.tar"

    def fail_chmod(_descriptor: int) -> None:
        raise PermissionError("injected chmod failure")

    monkeypatch.setattr(backup_module, "_chmod_directory", fail_chmod)

    with pytest.raises(RExecOpValidationError, match="output parent"):
        create_runtime_backup(root, output=output, now=NOW)

    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../../../escaped-by-restore.txt",
        "/absolute.txt",
        "C:/drive-absolute.txt",
        "nested\\ambiguous.txt",
        "nested//not-normalized.txt",
        "nested/control\nname.txt",
        "nested/cafe\u0301.txt",
        "nested/format\u202ename.txt",
        "nested/zero\x00name.txt",
        "operations/NUL",
        "operations/file.",
        "operations/file ",
        "operations/file:stream",
    ],
)
def test_backup_restore_rejects_unsafe_paths_without_writes(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    injected = b"injected"
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[
            (RUNTIME_MANIFEST, runtime_manifest, "file"),
            (unsafe_name, injected, "file"),
        ],
        manifest_files=[
            _backup_entry(RUNTIME_MANIFEST, runtime_manifest),
            _backup_entry(unsafe_name, injected),
        ],
    )
    target = tmp_path / "destination" / ".rexecop"
    outside = tmp_path / "escaped-by-restore.txt"

    with pytest.raises(RExecOpValidationError, match="backup member path is invalid"):
        restore_runtime_backup(archive=archive, target_root=target, manifest=manifest)

    assert not target.exists()
    assert not target.parent.exists()
    assert not outside.exists()


def test_backup_restore_rejects_portable_casefold_collision_before_staging(
    tmp_path: Path,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    upper = b"upper"
    lower = b"lower"
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[
            (RUNTIME_MANIFEST, runtime_manifest, "file"),
            ("operations/Foo.json", upper, "file"),
            ("operations/foo.json", lower, "file"),
        ],
        manifest_files=[
            _backup_entry(RUNTIME_MANIFEST, runtime_manifest),
            _backup_entry("operations/Foo.json", upper),
            _backup_entry("operations/foo.json", lower),
        ],
    )
    target = tmp_path / "target"

    with pytest.raises(RExecOpValidationError, match="portable path collision"):
        restore_runtime_backup(archive=archive, target_root=target, manifest=manifest)

    assert not target.exists()
    assert not list(tmp_path.glob(".target.restore-*"))


def test_backup_restore_rejects_path_tree_prefix_collision_before_staging(
    tmp_path: Path,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    parent_file = b"parent"
    child_file = b"child"
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[
            (RUNTIME_MANIFEST, runtime_manifest, "file"),
            ("operations", parent_file, "file"),
            ("operations/operation.json", child_file, "file"),
        ],
        manifest_files=[
            _backup_entry(RUNTIME_MANIFEST, runtime_manifest),
            _backup_entry("operations", parent_file),
            _backup_entry("operations/operation.json", child_file),
        ],
    )
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(RExecOpValidationError, match="path tree collision"):
        restore_runtime_backup(archive=archive, target_root=target, manifest=manifest)

    assert target.is_dir()
    assert list(target.iterdir()) == []
    assert not list(tmp_path.glob(".target.restore-*"))


def test_backup_restore_rejects_unmanifested_member_and_preserves_target(
    tmp_path: Path,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[
            (RUNTIME_MANIFEST, runtime_manifest, "file"),
            ("unmanifested.txt", b"injected", "file"),
        ],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )
    target = tmp_path / "target"
    target.mkdir()
    original = b'{"existing": true}\n'
    (target / RUNTIME_MANIFEST).write_bytes(original)

    with pytest.raises(RExecOpValidationError, match="member set mismatch"):
        restore_runtime_backup(archive=archive, target_root=target, manifest=manifest)

    assert sorted(path.name for path in target.iterdir()) == [RUNTIME_MANIFEST]
    assert (target / RUNTIME_MANIFEST).read_bytes() == original
    assert not (target / "unmanifested.txt").exists()


def test_backup_restore_rejects_duplicate_archive_member(tmp_path: Path) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[
            (RUNTIME_MANIFEST, runtime_manifest, "file"),
            (RUNTIME_MANIFEST, runtime_manifest, "file"),
        ],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )

    with pytest.raises(RExecOpValidationError, match="duplicate paths"):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )
    assert not (tmp_path / "target").exists()


def test_backup_restore_rejects_duplicate_manifest_path(tmp_path: Path) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    entry = _backup_entry(RUNTIME_MANIFEST, runtime_manifest)
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[entry, entry],
    )

    with pytest.raises(RExecOpValidationError, match="duplicate paths"):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )
    assert not (tmp_path / "target").exists()


def test_backup_restore_rejects_file_count_mismatch(tmp_path: Path) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
        file_count=2,
    )

    with pytest.raises(RExecOpValidationError, match="backup manifest is invalid"):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize("manifest_text", ["[]", '{"schema": 1}', "{not-json"])
def test_backup_restore_rejects_malformed_backup_manifest(
    tmp_path: Path,
    manifest_text: str,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )
    manifest.write_text(manifest_text, encoding="utf-8")

    with pytest.raises(RExecOpValidationError):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )
    assert not (tmp_path / "target").exists()


def test_backup_restore_requires_archived_runtime_manifest(tmp_path: Path) -> None:
    data = b"not a runtime root"
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[("data.txt", data, "file")],
        manifest_files=[_backup_entry("data.txt", data)],
    )

    with pytest.raises(RExecOpValidationError, match="runtime manifest is required"):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )
    assert not (tmp_path / "target").exists()


def test_backup_restore_rejects_truncated_archive(tmp_path: Path) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    payload = b"x" * 2048
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[
            (RUNTIME_MANIFEST, runtime_manifest, "file"),
            ("payload.bin", payload, "file"),
        ],
        manifest_files=[
            _backup_entry(RUNTIME_MANIFEST, runtime_manifest),
            _backup_entry("payload.bin", payload),
        ],
    )
    with tarfile.open(archive, "r") as handle:
        payload_member = handle.getmember("payload.bin")
        truncate_at = payload_member.offset_data + len(payload) - 1
    archive.write_bytes(archive.read_bytes()[:truncate_at])

    with pytest.raises(RExecOpValidationError, match="backup archive is invalid"):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )
    assert not (tmp_path / "target").exists()


def test_backup_restore_rejects_digest_mismatch_without_mutating_target(
    tmp_path: Path,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[
            {
                "path": RUNTIME_MANIFEST,
                "sha256": hashlib.sha256(b"different").hexdigest(),
            }
        ],
    )
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(RExecOpValidationError, match="backup digest mismatch"):
        restore_runtime_backup(archive=archive, target_root=target, manifest=manifest)

    assert target.is_dir()
    assert list(target.iterdir()) == []


def test_backup_restore_rejects_symlink_target_ancestor(tmp_path: Path) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(RExecOpValidationError, match="ancestors must be real directories"):
        restore_runtime_backup(
            archive=archive,
            target_root=linked_parent / "target",
            manifest=manifest,
        )
    assert not (real_parent / "target").exists()


@pytest.mark.parametrize("linked_input", ["archive", "manifest"])
def test_backup_restore_rejects_symlinked_inputs(
    tmp_path: Path,
    linked_input: str,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )
    selected = archive if linked_input == "archive" else manifest
    real = selected.with_name(f"real-{selected.name}")
    selected.rename(real)
    selected.symlink_to(real)

    with pytest.raises(RExecOpValidationError):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )

    assert not (tmp_path / "target").exists()


def test_backup_restore_rejects_archive_path_swap_after_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )
    opened_archive = tmp_path / "opened.tar"
    replacement = tmp_path / "replacement.tar"
    replacement.write_bytes(b"not a tar archive")
    original_validate = backup_module._validate_raw_ustar
    swapped = False

    def swap_archive(archive_stream: Any) -> None:
        nonlocal swapped
        if not swapped:
            archive.rename(opened_archive)
            archive.symlink_to(replacement)
            swapped = True
        original_validate(archive_stream)

    monkeypatch.setattr(backup_module, "_validate_raw_ustar", swap_archive)
    target = tmp_path / "target"

    with pytest.raises(RExecOpValidationError, match="archive changed during restore"):
        restore_runtime_backup(
            archive=archive,
            target_root=target,
            manifest=manifest,
        )

    assert not target.exists()


def test_backup_restore_reads_open_manifest_descriptor_after_path_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )
    opened_manifest = tmp_path / "opened.manifest.json"
    original_read = backup_module._read_bounded_regular_descriptor
    swapped = False

    def swap_manifest(*args: Any, **kwargs: Any) -> bytes:
        nonlocal swapped
        if not swapped:
            manifest.rename(opened_manifest)
            manifest.write_text("not json", encoding="utf-8")
            swapped = True
        return original_read(*args, **kwargs)

    monkeypatch.setattr(
        backup_module,
        "_read_bounded_regular_descriptor",
        swap_manifest,
    )
    target = tmp_path / "target"

    restored = restore_runtime_backup(
        archive=archive,
        target_root=target,
        manifest=manifest,
    )

    assert restored["status"] == "restored"
    assert (target / RUNTIME_MANIFEST).read_bytes() == runtime_manifest


def test_backup_restore_rejects_post_open_archive_growth_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )
    opening_size = archive.stat().st_size
    original_open = backup_module._open_regular_at
    grew = False

    def open_then_grow(*args: Any, **kwargs: Any):
        nonlocal grew
        opened = original_open(*args, **kwargs)
        if not grew and args[1] == archive.name:
            with archive.open("ab") as output:
                output.write(b"\0" * tarfile.BLOCKSIZE)
            grew = True
        return opened

    monkeypatch.setattr(backup_module, "_BACKUP_ARCHIVE_MAX_BYTES", opening_size)
    monkeypatch.setattr(backup_module, "_open_regular_at", open_then_grow)
    target = tmp_path / "restore-parent" / "target"

    with pytest.raises(RExecOpValidationError, match="archive exceeds size limit"):
        restore_runtime_backup(
            archive=archive,
            target_root=target,
            manifest=manifest,
        )

    assert not target.parent.exists()


def test_backup_restore_rejects_target_ancestor_swap_before_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )
    target_parent = tmp_path / "destination"
    target_parent.mkdir()
    moved_parent = tmp_path / "moved-destination"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_extract = backup_module._extract_validated_members

    def extract_then_swap(*args: Any, **kwargs: Any) -> None:
        original_extract(*args, **kwargs)
        target_parent.rename(moved_parent)
        target_parent.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(
        backup_module,
        "_extract_validated_members",
        extract_then_swap,
    )

    with pytest.raises(RExecOpValidationError, match="target changed"):
        restore_runtime_backup(
            archive=archive,
            target_root=target_parent / "target",
            manifest=manifest,
        )

    assert list(outside.iterdir()) == []
    assert list(moved_parent.iterdir()) == []


def test_backup_restore_chmod_failure_cleans_new_target_parents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )

    def fail_chmod(_descriptor: int) -> None:
        raise PermissionError("injected chmod failure")

    monkeypatch.setattr(backup_module, "_chmod_directory", fail_chmod)

    with pytest.raises(RExecOpValidationError, match="target ancestors"):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "new" / "nested" / "target",
            manifest=manifest,
        )

    assert not (tmp_path / "new").exists()


def test_backup_restore_wraps_target_inspection_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )
    target = tmp_path / "target"
    target.mkdir()
    target_identity = (target.stat().st_dev, target.stat().st_ino)
    original_listdir = os.listdir

    def denied_listdir(path: str | bytes | int | os.PathLike[str] | os.PathLike[bytes]):
        if isinstance(path, int):
            metadata = os.fstat(path)
            if (metadata.st_dev, metadata.st_ino) == target_identity:
                raise PermissionError("target inspection denied")
        if path == target:
            raise PermissionError("target inspection denied")
        return original_listdir(path)

    monkeypatch.setattr(os, "listdir", denied_listdir)

    with pytest.raises(
        RExecOpValidationError,
        match="backup restore filesystem operation failed",
    ) as raised:
        restore_runtime_backup(archive=archive, target_root=target, manifest=manifest)

    assert isinstance(raised.value.__cause__, PermissionError)
    assert target.is_dir()


def test_backup_restore_rejects_non_regular_member(tmp_path: Path) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[
            (RUNTIME_MANIFEST, runtime_manifest, "file"),
            ("linked-runtime-manifest.json", b"", "symlink"),
        ],
        manifest_files=[
            _backup_entry(RUNTIME_MANIFEST, runtime_manifest),
            _backup_entry("linked-runtime-manifest.json", b""),
        ],
    )

    with pytest.raises(RExecOpValidationError, match="non-regular member"):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize("member_type", ["pax", "contiguous", "sparse"])
def test_backup_restore_rejects_extended_regular_member_semantics(
    tmp_path: Path,
    member_type: str,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, member_type)],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )

    with pytest.raises(RExecOpValidationError, match="non-regular member"):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )
    assert not (tmp_path / "target").exists()


def test_backup_restore_rejects_global_pax_metadata(tmp_path: Path) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
        global_pax_headers={"comment": "untrusted-extension"},
    )

    with pytest.raises(RExecOpValidationError, match="extension metadata"):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize(
    ("archive_variant", "error"),
    [
        ("nonzero-trailer", "non-zero trailing data"),
        ("concatenated", "non-zero trailing data"),
        ("gnu", "not strict USTAR"),
        ("aregtype", "non-regular member"),
    ],
)
def test_backup_restore_rejects_noncanonical_tar_encodings(
    tmp_path: Path,
    archive_variant: str,
    error: str,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )
    original = archive.read_bytes()
    if archive_variant == "nonzero-trailer":
        archive.write_bytes(original + b"nonzero")
    elif archive_variant == "concatenated":
        archive.write_bytes(original + original)
    elif archive_variant == "gnu":
        with tarfile.open(archive, "w", format=tarfile.GNU_FORMAT) as handle:
            member = tarfile.TarInfo(RUNTIME_MANIFEST)
            member.size = len(runtime_manifest)
            handle.addfile(member, io.BytesIO(runtime_manifest))
    else:
        mutated = bytearray(original)
        mutated[156] = 0
        archive.write_bytes(mutated)

    with pytest.raises(RExecOpValidationError, match=error):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )

    assert not (tmp_path / "target").exists()


def test_backup_restore_rejects_nonzero_member_padding(tmp_path: Path) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )
    mutated = bytearray(archive.read_bytes())
    mutated[tarfile.BLOCKSIZE + len(runtime_manifest)] = 1
    archive.write_bytes(mutated)

    with pytest.raises(RExecOpValidationError, match="non-zero member padding"):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )

    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize(
    ("checksum_variant", "error"),
    [
        ("base-256", "checksum is not canonical USTAR"),
        ("incorrect", "checksum mismatch"),
    ],
)
def test_backup_restore_rejects_noncanonical_or_incorrect_checksum(
    tmp_path: Path,
    checksum_variant: str,
    error: str,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )
    mutated = bytearray(archive.read_bytes())
    header = mutated[: tarfile.BLOCKSIZE]
    checksum = sum(header[:148]) + (8 * ord(" ")) + sum(header[156:])
    if checksum_variant == "base-256":
        encoded = bytearray(checksum.to_bytes(8, "big"))
        encoded[0] |= 0x80
        mutated[148:156] = encoded
    else:
        mutated[148:156] = b"000000\0 "
    archive.write_bytes(mutated)

    with pytest.raises(RExecOpValidationError, match=error):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )

    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize(
    ("limit_name", "error"),
    [
        ("sidecar", "backup manifest exceeds size limit"),
        ("file-count", "backup manifest is invalid"),
        ("archive", "backup archive exceeds size limit"),
        ("member", "backup archive member exceeds size limit"),
        ("expanded", "backup archive expanded size exceeds limit"),
    ],
)
def test_backup_restore_enforces_resource_limits_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    error: str,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    payload = b"payload"
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[
            (RUNTIME_MANIFEST, runtime_manifest, "file"),
            ("operations/record.json", payload, "file"),
        ],
        manifest_files=[
            _backup_entry(RUNTIME_MANIFEST, runtime_manifest),
            _backup_entry("operations/record.json", payload),
        ],
    )
    if limit_name == "sidecar":
        monkeypatch.setattr(
            backup_module,
            "_BACKUP_SIDECAR_MAX_BYTES",
            manifest.stat().st_size - 1,
        )
    elif limit_name == "file-count":
        monkeypatch.setattr(backup_module, "_BACKUP_MAX_FILES", 1)
    elif limit_name == "archive":
        monkeypatch.setattr(
            backup_module,
            "_BACKUP_ARCHIVE_MAX_BYTES",
            archive.stat().st_size - 1,
        )
    elif limit_name == "member":
        monkeypatch.setattr(
            backup_module,
            "_BACKUP_MEMBER_MAX_BYTES",
            max(len(runtime_manifest), len(payload)) - 1,
        )
    else:
        monkeypatch.setattr(
            backup_module,
            "_BACKUP_TOTAL_MAX_BYTES",
            len(runtime_manifest) + len(payload) - 1,
        )
    target = tmp_path / "target"

    with pytest.raises(RExecOpValidationError, match=error):
        restore_runtime_backup(archive=archive, target_root=target, manifest=manifest)

    assert not target.exists()
    assert not list(tmp_path.glob(".target.restore-*"))


@pytest.mark.parametrize(
    ("constant", "limit", "error"),
    [
        ("_BACKUP_MAX_FILES", 1, "file count exceeds limit"),
        ("_BACKUP_MEMBER_MAX_BYTES", 1, "member exceeds size limit"),
        ("_BACKUP_TOTAL_MAX_BYTES", 1, "expanded size exceeds limit"),
    ],
)
def test_backup_create_enforces_snapshot_resource_limits_before_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    limit: int,
    error: str,
) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    monkeypatch.setattr(backup_module, constant, limit)
    output = tmp_path / "backup.tar"

    with pytest.raises(RExecOpValidationError, match=error):
        create_runtime_backup(root, output=output, now=NOW)

    assert not output.exists()
    assert not output.with_suffix(".manifest.json").exists()


def test_backup_create_enforces_archive_size_limit_without_final_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    monkeypatch.setattr(backup_module, "_BACKUP_ARCHIVE_MAX_BYTES", 1)
    output = tmp_path / "backup.tar"

    with pytest.raises(RExecOpValidationError, match="archive exceeds size limit"):
        create_runtime_backup(root, output=output, now=NOW)

    assert not output.exists()
    assert not output.with_suffix(".manifest.json").exists()


@pytest.mark.parametrize(
    ("runtime_manifest", "error"),
    [
        (b"not-json", "backup runtime manifest is invalid"),
        (b"[]", "backup runtime manifest is invalid"),
        (
            _runtime_manifest_bytes(version="0.2.24a0"),
            "runtime_root_new_root_required",
        ),
    ],
)
def test_backup_restore_rejects_invalid_or_incompatible_runtime_manifest(
    tmp_path: Path,
    runtime_manifest: bytes,
    error: str,
) -> None:
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[(RUNTIME_MANIFEST, runtime_manifest, "file")],
        manifest_files=[_backup_entry(RUNTIME_MANIFEST, runtime_manifest)],
    )

    with pytest.raises(RExecOpValidationError, match=error):
        restore_runtime_backup(
            archive=archive,
            target_root=tmp_path / "target",
            manifest=manifest,
        )
    assert not (tmp_path / "target").exists()


def test_backup_restore_directly_promotes_into_existing_empty_target(
    tmp_path: Path,
) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[
            (RUNTIME_MANIFEST, runtime_manifest, "file"),
            ("operations/operation.json", b'{"id": "restored"}\n', "file"),
        ],
        manifest_files=[
            _backup_entry(RUNTIME_MANIFEST, runtime_manifest),
            _backup_entry("operations/operation.json", b'{"id": "restored"}\n'),
        ],
    )
    target = tmp_path / "target"
    target.mkdir()
    previous_identity = (target.stat().st_dev, target.stat().st_ino)

    restored = restore_runtime_backup(
        archive=archive,
        target_root=target,
        manifest=manifest,
    )

    assert restored["status"] == "restored"
    assert (target.stat().st_dev, target.stat().st_ino) != previous_identity
    assert (target / RUNTIME_MANIFEST).read_bytes() == runtime_manifest
    assert (target / "operations" / "operation.json").is_file()
    assert not list(tmp_path.glob(".target.restore-*"))
    assert not list(tmp_path.glob(".target.previous-*"))


def test_backup_restore_rejects_nonempty_target_and_preserves_it(tmp_path: Path) -> None:
    runtime_manifest = _runtime_manifest_bytes()
    archive, manifest = _write_backup_fixture(
        tmp_path,
        members=[
            (RUNTIME_MANIFEST, runtime_manifest, "file"),
            ("operations/operation.json", b'{"id": "restored"}\n', "file"),
        ],
        manifest_files=[
            _backup_entry(RUNTIME_MANIFEST, runtime_manifest),
            _backup_entry("operations/operation.json", b'{"id": "restored"}\n'),
        ],
    )
    target = tmp_path / "target"
    target.mkdir()
    original = b'{"placeholder": true}\n'
    (target / RUNTIME_MANIFEST).write_bytes(original)
    previous_identity = (target.stat().st_dev, target.stat().st_ino)

    with pytest.raises(RExecOpValidationError, match="absent or empty target"):
        restore_runtime_backup(archive=archive, target_root=target, manifest=manifest)

    assert (target.stat().st_dev, target.stat().st_ino) == previous_identity
    assert list(target.iterdir()) == [target / RUNTIME_MANIFEST]
    assert (target / RUNTIME_MANIFEST).read_bytes() == original
    assert not list(tmp_path.glob(".target.restore-*"))
    assert not list(tmp_path.glob(".target.previous-*"))


def test_recovery_replay_drill_worker_restart_does_not_duplicate_start(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
        requested_by="trigger:inbox:job.json",
    )
    operation.state = OperationState.RUNNING.value
    operation.metadata["execution_cursor"] = {"next_step_index": 0}
    controller.store.save_operation(operation)

    first = run_worker(controller, once=True, watchdog=True)
    assert first == []
    updated = controller.get_operation(operation.id)
    assert updated.state == OperationState.FAILED.value

    second = run_worker(controller, once=True, watchdog=True)
    assert second == []
    final = controller.get_operation(operation.id)
    assert final.state == OperationState.FAILED.value


def test_runtime_reconstruction_status_reports_rebuildable_completed_operation(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    operation = controller.plan(
        profile_path=PROFILE,
        environment_path=ENVIRONMENT,
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
    )
    controller.start(operation.id)

    payload = collect_runtime_reconstruction_status(controller.store)

    assert payload["schema"] == "rexecop.runtime_reconstruction.v0.1"
    assert payload["status"] == "reconstructable"
    assert payload["summary"]["reconstructable_count"] == 1
    item = payload["operations"][0]
    assert item["operation_id"] == operation.id
    assert item["status"] == "reconstructable"
    assert item["inputs"]["operation_record"]["status"] == "present"
    assert item["inputs"]["plan_record"]["status"] == "present"
    assert item["inputs"]["receipt_export"]["status"] == "present"
    assert item["blockers"] == []


def test_runtime_reconstruction_status_blocks_missing_plan_and_active_state(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    now = NOW.isoformat()
    operation = Operation(
        id="op-running-reconstruct",
        profile="runtime-fixture",
        environment="runtime-fixture",
        intent="inspect_fixture_state",
        target="fixture-target",
        mode="dry_run",
        requested_by="operator",
        state=OperationState.RUNNING.value,
        created_at=now,
        updated_at=now,
        correlation_id="corr-running-reconstruct",
    )
    controller.store.save_operation(operation)

    payload = collect_runtime_reconstruction_status(controller.store)

    assert payload["status"] == "blocked"
    assert payload["summary"]["blocked_count"] == 1
    item = payload["operations"][0]
    assert item["status"] == "blocked"
    assert item["blockers"] == [
        "plan_record_missing",
        "active_state_requires_runtime_recover",
    ]
    assert payload["safe_next_actions"] == [
        "rexecop runtime recover --json",
        "rexecop runtime reconstruct-status --json",
    ]


def test_cli_runtime_recover_and_backup(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    init = runner.invoke(app, ["--root", str(root), "init"])
    assert init.exit_code == 0, init.output

    recover = runner.invoke(app, ["--root", str(root), "runtime", "recover", "--json"])
    assert recover.exit_code == 0, recover.output
    assert '"schema": "rexecop.runtime_recovery.v0.1"' in recover.output

    backup = runner.invoke(
        app,
        ["--root", str(root), "backup", "create", "--output", str(tmp_path / "bundle.tar")],
    )
    assert backup.exit_code == 0, backup.output
    assert '"status": "created"' in backup.output


def test_cli_backup_create_rejects_configured_backend_mismatch_before_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REXECOP_STORAGE", "file")
    root = tmp_path / "file-root"
    initialize_runtime_root(root, backend="file")
    output = tmp_path / "bundle.tar"

    result = runner.invoke(
        app,
        [
            "--root",
            str(root),
            "--storage",
            "sqlite",
            "backup",
            "create",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 1
    assert "error: runtime_root_storage_backend_mismatch" in result.output
    assert not output.exists()
    assert not output.with_suffix(".manifest.json").exists()


def test_cli_backup_restore_accepts_new_target_then_factory_opens_it(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    initialize_runtime_root(source, backend="file")
    created = create_runtime_backup(source, output=tmp_path / "backup.tar", now=NOW)
    target = tmp_path / "restored"

    result = runner.invoke(
        app,
        [
            "--root",
            str(target),
            "backup",
            "restore",
            "--archive",
            str(created["archive"]),
            "--manifest",
            str(created["manifest"]),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "restored"
    assert create_store(target, backend="file").root == target


def test_cli_backup_create_contains_suffixless_existing_output_error(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    initialize_runtime_root(root)
    output = tmp_path / "existing-output"
    original = b"preserve\n"
    output.write_bytes(original)

    result = runner.invoke(
        app,
        [
            "--root",
            str(root),
            "backup",
            "create",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 1
    assert result.exception is not None
    assert "error:" in result.output
    assert output.read_bytes() == original


def test_cli_runtime_reconstruct_status(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    init = runner.invoke(app, ["--root", str(root), "init"])
    assert init.exit_code == 0, init.output

    result = runner.invoke(
        app,
        ["--root", str(root), "runtime", "reconstruct-status", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert '"schema": "rexecop.runtime_reconstruction.v0.1"' in result.output
    assert '"status": "reconstructable"' in result.output
