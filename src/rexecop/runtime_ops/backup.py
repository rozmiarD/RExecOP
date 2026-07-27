from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tarfile
import tempfile
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO

from rexecop import __version__
from rexecop.errors import RExecOpValidationError
from rexecop.runtime.init import RUNTIME_DIRECTORIES, RUNTIME_MANIFEST
from rexecop.runtime.root_compatibility import require_runtime_root_compatible
from rexecop.storage.atomic import secure_directory

BACKUP_MANIFEST_SCHEMA = "rexecop.runtime_backup.v0.1"
BACKUP_ARCHIVE_NAME = "runtime_store.tar"
MANIFEST_NAME = "backup_manifest.json"
_BACKUP_MANIFEST_KEYS = {
    "schema",
    "rexecop_version",
    "runtime_root_fingerprint",
    "created_at",
    "file_count",
    "files",
    "archive",
}
_BACKUP_FILE_KEYS = {"path", "sha256"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{16}$")
_RUNTIME_MANIFEST_MAX_BYTES = 1024 * 1024
_BACKUP_SIDECAR_MAX_BYTES = 1024 * 1024
_BACKUP_MAX_FILES = 10_000
_BACKUP_ARCHIVE_MAX_BYTES = 512 * 1024 * 1024
_BACKUP_MEMBER_MAX_BYTES = 64 * 1024 * 1024
_BACKUP_TOTAL_MAX_BYTES = 256 * 1024 * 1024
_MEMBER_NAME_MAX_BYTES = 4096
_MEMBER_PART_MAX_BYTES = 255
_ARCHIVE_SUFFIXES = (".tar", ".tgz", ".tar.gz")
_WINDOWS_RESERVED_BASENAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}

@dataclass(frozen=True, slots=True)
class _SnapshotFile:
    name: str
    path: Path
    sha256: str
    size: int


@dataclass(slots=True)
class _DirectoryChain:
    path: Path
    descriptors: list[int]
    identities: tuple[tuple[int, int], ...]
    created: list[tuple[int, str]]

    @property
    def descriptor(self) -> int:
        return self.descriptors[-1]

    def cleanup_created(self) -> None:
        for parent_descriptor, name in reversed(self.created):
            try:
                os.rmdir(name, dir_fd=parent_descriptor)
            except OSError:
                break
        self.created.clear()

    def close(self) -> None:
        for descriptor in reversed(self.descriptors):
            os.close(descriptor)
        self.descriptors.clear()

    def detach_final(self) -> int:
        descriptor = self.descriptors.pop()
        self.close()
        return descriptor


class _DigestingReader:
    def __init__(self, handle: BinaryIO) -> None:
        self.handle = handle
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        data = self.handle.read(size)
        self.digest.update(data)
        self.size += len(data)
        return data


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise RExecOpValidationError(
            "runtime backup requires no-follow directory descriptor support"
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _chmod_directory(descriptor: int) -> None:
    os.fchmod(descriptor, 0o700)


def _open_directory_chain(
    path: Path,
    *,
    create: bool,
    error: str,
) -> _DirectoryChain:
    absolute = _absolute_path(path)
    descriptors: list[int] = []
    identities: list[tuple[int, int]] = []
    created: list[tuple[int, str]] = []
    try:
        descriptor = os.open("/", _directory_flags())
        descriptors.append(descriptor)
        metadata = os.fstat(descriptor)
        identities.append((metadata.st_dev, metadata.st_ino))
        for part in absolute.parts[1:]:
            parent_descriptor = descriptors[-1]
            created_here = False
            try:
                child = os.open(part, _directory_flags(), dir_fd=parent_descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=parent_descriptor)
                    created.append((parent_descriptor, part))
                    created_here = True
                except FileExistsError:
                    pass
                child = os.open(part, _directory_flags(), dir_fd=parent_descriptor)
            descriptors.append(child)
            if created_here:
                _chmod_directory(child)
            metadata = os.fstat(child)
            identities.append((metadata.st_dev, metadata.st_ino))
    except Exception as exc:
        chain = _DirectoryChain(absolute, descriptors, tuple(identities), created)
        chain.cleanup_created()
        chain.close()
        if isinstance(exc, RExecOpValidationError):
            raise
        raise RExecOpValidationError(error) from exc
    return _DirectoryChain(absolute, descriptors, tuple(identities), created)


def _revalidate_directory_chain(chain: _DirectoryChain, *, error: str) -> None:
    current = _open_directory_chain(chain.path, create=False, error=error)
    try:
        if current.identities != chain.identities:
            raise RExecOpValidationError(error)
    finally:
        current.close()


def _resolve_backup_destinations(
    output: Path,
    *,
    observed_at: datetime,
) -> tuple[Path, Path]:
    output_path = _absolute_path(output)
    if output_path.name.endswith(_ARCHIVE_SUFFIXES):
        archive_path = output_path
    else:
        stamp = observed_at.strftime("%Y%m%dT%H%M%SZ")
        archive_path = output_path / f"rexecop-runtime-backup-{stamp}.tar"
    manifest_path = archive_path.with_name(archive_path.stem + ".manifest.json")
    return archive_path, manifest_path


def _reject_in_source_destinations(
    root: Path,
    *,
    archive: Path,
    manifest: Path,
) -> None:
    resolved_root = root.resolve(strict=False)
    for destination in (archive, manifest):
        resolved_destination = destination.resolve(strict=False)
        if destination.is_relative_to(root) or resolved_destination.is_relative_to(resolved_root):
            raise RExecOpValidationError(
                "backup archive and manifest must be outside the runtime root"
            )


def _open_runtime_root(root: Path) -> int:
    chain = _open_directory_chain(
        root,
        create=False,
        error="runtime root and its ancestors must be real directories",
    )
    return chain.detach_final()


def _minimal_backup_paths() -> tuple[str, ...]:
    minimal: list[str] = []
    for candidate in _backup_paths():
        if not any(
            candidate == selected or candidate.startswith(f"{selected}/") for selected in minimal
        ):
            minimal.append(candidate)
    return tuple(minimal)


def _metadata_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_child(
    parent_descriptor: int,
    name: str,
    *,
    directory: bool,
    observed: os.stat_result,
) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if directory:
        flags |= os.O_DIRECTORY
    elif hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    opened = os.fstat(descriptor)
    if _metadata_identity(opened) != _metadata_identity(observed):
        os.close(descriptor)
        raise RExecOpValidationError("runtime backup source changed during snapshot")
    return descriptor


def _copy_regular_file(
    source_descriptor: int,
    *,
    observed: os.stat_result,
    destination: Path,
    name: str,
) -> _SnapshotFile:
    if observed.st_size > _BACKUP_MEMBER_MAX_BYTES:
        raise RExecOpValidationError("runtime backup member exceeds size limit")
    secure_directory(destination.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    output_descriptor = os.open(destination, flags, 0o600)
    digest = hashlib.sha256()
    copied = 0
    try:
        with os.fdopen(output_descriptor, "wb") as output:
            while True:
                chunk = os.read(source_descriptor, 65536)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > _BACKUP_MEMBER_MAX_BYTES:
                    raise RExecOpValidationError("runtime backup member exceeds size limit")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    after = os.fstat(source_descriptor)
    if copied != observed.st_size or _metadata_identity(after) != _metadata_identity(observed):
        destination.unlink(missing_ok=True)
        raise RExecOpValidationError("runtime backup source changed during snapshot")
    return _SnapshotFile(
        name=name,
        path=destination,
        sha256=digest.hexdigest(),
        size=copied,
    )


def _snapshot_selected_files(root_descriptor: int, snapshot_root: Path) -> list[_SnapshotFile]:
    records: list[_SnapshotFile] = []
    total_size = 0
    root_before = os.fstat(root_descriptor)

    def snapshot_entry(
        parent_descriptor: int,
        entry_name: str,
        relative_parts: tuple[str, ...],
        *,
        required: bool = False,
    ) -> None:
        nonlocal total_size
        try:
            observed = os.stat(
                entry_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError as exc:
            if required:
                raise RExecOpValidationError("source runtime manifest is required") from exc
            return
        if stat.S_ISLNK(observed.st_mode):
            raise RExecOpValidationError(
                "runtime backup does not support symbolic links in selected paths"
            )
        if required and not stat.S_ISREG(observed.st_mode):
            raise RExecOpValidationError("source runtime manifest must be a real regular file")
        if stat.S_ISDIR(observed.st_mode):
            descriptor = _open_child(
                parent_descriptor,
                entry_name,
                directory=True,
                observed=observed,
            )
            try:
                with os.scandir(descriptor) as iterator:
                    child_names = sorted(entry.name for entry in iterator)
                for child_name in child_names:
                    snapshot_entry(
                        descriptor,
                        child_name,
                        (*relative_parts, child_name),
                    )
                if _metadata_identity(os.fstat(descriptor)) != _metadata_identity(observed):
                    raise RExecOpValidationError("runtime backup source changed during snapshot")
            finally:
                os.close(descriptor)
            return
        if not stat.S_ISREG(observed.st_mode):
            raise RExecOpValidationError(
                "runtime backup selected paths contain a non-regular entry"
            )
        if entry_name.endswith(".tmp"):
            return
        name = _validated_member_name(PurePosixPath(*relative_parts).as_posix())
        if len(records) >= _BACKUP_MAX_FILES:
            raise RExecOpValidationError("runtime backup file count exceeds limit")
        if total_size + observed.st_size > _BACKUP_TOTAL_MAX_BYTES:
            raise RExecOpValidationError("runtime backup expanded size exceeds limit")
        descriptor = _open_child(
            parent_descriptor,
            entry_name,
            directory=False,
            observed=observed,
        )
        try:
            record = _copy_regular_file(
                descriptor,
                observed=observed,
                destination=snapshot_root.joinpath(*relative_parts),
                name=name,
            )
        finally:
            os.close(descriptor)
        records.append(record)
        total_size += record.size

    for selected in _minimal_backup_paths():
        parts = PurePosixPath(selected).parts
        if len(parts) != 1:
            raise RExecOpValidationError("runtime backup selected path is invalid")
        snapshot_entry(
            root_descriptor,
            parts[0],
            parts,
            required=selected == RUNTIME_MANIFEST,
        )
    if _metadata_identity(os.fstat(root_descriptor)) != _metadata_identity(root_before):
        raise RExecOpValidationError("runtime backup source changed during snapshot")
    _validate_path_tree(record.name for record in records)
    return records


def _validate_snapshot_runtime_manifest(records: list[_SnapshotFile]) -> None:
    runtime_manifest = next(
        (record for record in records if record.name == RUNTIME_MANIFEST),
        None,
    )
    if runtime_manifest is None:
        raise RExecOpValidationError("source runtime manifest is required")
    if runtime_manifest.size > _RUNTIME_MANIFEST_MAX_BYTES:
        raise RExecOpValidationError("source runtime manifest exceeds size limit")
    _validate_runtime_manifest(
        _read_bounded_regular_path(
            runtime_manifest.path,
            maximum_bytes=_RUNTIME_MANIFEST_MAX_BYTES,
            error="source runtime manifest exceeds size limit or is not a regular file",
        )
    )


def _load_snapshot_scanner() -> Callable[..., list[Any]]:
    try:
        from rexecop.security.secret_scan import scan_path
    except Exception as exc:
        raise RExecOpValidationError("runtime backup secret scan is unavailable") from exc
    if not callable(scan_path):
        raise RExecOpValidationError("runtime backup secret scan is unavailable")
    return scan_path


def _scan_snapshot(identity: str, records: list[_SnapshotFile]) -> list[str]:
    scanner = _load_snapshot_scanner()
    try:
        findings: list[str] = []
        for record in records:
            for finding in scanner(
                scope="runtime_backup",
                identity=identity,
                path=str(record.path),
            ):
                findings.append(finding.render())
        return findings
    except Exception as exc:
        raise RExecOpValidationError("runtime backup secret scan failed") from exc


def _write_strict_archive(output: BinaryIO, records: list[_SnapshotFile]) -> None:
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for record in records:
            member = tarfile.TarInfo(record.name)
            member.type = tarfile.REGTYPE
            member.size = record.size
            member.mode = 0o600
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.mtime = 0
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            descriptor = os.open(record.path, flags)
            with os.fdopen(descriptor, "rb") as source:
                before = os.fstat(source.fileno())
                reader = _DigestingReader(source)
                archive.addfile(member, reader)
                after = os.fstat(source.fileno())
            if (
                reader.size != record.size
                or reader.digest.hexdigest() != record.sha256
                or _metadata_identity(after) != _metadata_identity(before)
            ):
                raise RExecOpValidationError(
                    "runtime backup snapshot changed during archive creation"
                )


def _manifest_bytes(
    *,
    root: Path,
    archive: Path,
    observed_at: datetime,
    records: list[_SnapshotFile],
) -> bytes:
    manifest = {
        "schema": BACKUP_MANIFEST_SCHEMA,
        "rexecop_version": __version__,
        "runtime_root_fingerprint": hashlib.sha256(str(root).encode()).hexdigest()[:16],
        "created_at": observed_at.isoformat(),
        "file_count": len(records),
        "files": [{"path": record.name, "sha256": record.sha256} for record in records],
        "archive": archive.name,
    }
    data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    if len(data) > _BACKUP_SIDECAR_MAX_BYTES:
        raise RExecOpValidationError("backup manifest exceeds size limit")
    return data


def _temporary_output(parent_descriptor: int, *, prefix: str) -> tuple[int, str]:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    for _ in range(100):
        name = f"{prefix}{secrets.token_hex(8)}.tmp"
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        try:
            os.fchmod(descriptor, 0o600)
        except OSError:
            os.close(descriptor)
            os.unlink(name, dir_fd=parent_descriptor)
            raise
        return descriptor, name
    raise RExecOpValidationError("unable to allocate temporary backup output")


def _publish_outputs(
    *,
    parent_descriptor: int,
    temporary_archive: str,
    temporary_manifest: str,
    archive: str,
    manifest: str,
) -> None:
    published: list[str] = []
    try:
        os.link(
            temporary_manifest,
            manifest,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        published.append(manifest)
        os.link(
            temporary_archive,
            archive,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        published.append(archive)
    except OSError:
        for name in reversed(published):
            try:
                os.unlink(name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        raise


def _create_runtime_backup(
    root: Path,
    *,
    archive_path: Path,
    manifest_path: Path,
    observed_at: datetime,
) -> dict[str, Any]:
    root_descriptor = _open_runtime_root(root)
    output_chain: _DirectoryChain | None = None
    temporary_archive: str | None = None
    temporary_manifest: str | None = None
    succeeded = False
    try:
        with tempfile.TemporaryDirectory(prefix="rexecop-backup-snapshot-") as raw:
            snapshot_root = Path(raw)
            snapshot_root.chmod(0o700)
            records = _snapshot_selected_files(root_descriptor, snapshot_root)
            _validate_snapshot_runtime_manifest(records)
            secret_findings = _scan_snapshot(root.name, records)
            if secret_findings:
                raise RExecOpValidationError(
                    "runtime backup blocked by secret scan: " + "; ".join(secret_findings[:5])
                )

            output_chain = _open_directory_chain(
                archive_path.parent,
                create=True,
                error="backup output parent must be a real directory",
            )
            archive_descriptor, temporary_archive = _temporary_output(
                output_chain.descriptor,
                prefix=f".{archive_path.name}.",
            )
            expected_files = {record.name: record.sha256 for record in records}
            with os.fdopen(archive_descriptor, "w+b") as archive_stream:
                _write_strict_archive(archive_stream, records)
                archive_stream.flush()
                os.fsync(archive_stream.fileno())
                if os.fstat(archive_stream.fileno()).st_size > _BACKUP_ARCHIVE_MAX_BYTES:
                    raise RExecOpValidationError("backup archive exceeds size limit")
                _validate_raw_ustar(archive_stream)
                archive_stream.seek(0)
                with tarfile.open(fileobj=archive_stream, mode="r:") as archive_handle:
                    _validate_archive(archive_handle, expected_files)

            sidecar = _manifest_bytes(
                root=root,
                archive=archive_path,
                observed_at=observed_at,
                records=records,
            )
            manifest_descriptor, temporary_manifest = _temporary_output(
                output_chain.descriptor,
                prefix=f".{manifest_path.name}.",
            )
            with os.fdopen(manifest_descriptor, "wb") as output:
                output.write(sidecar)
                output.flush()
                os.fsync(output.fileno())
            _revalidate_directory_chain(
                output_chain,
                error="backup output parent changed during creation",
            )
            _publish_outputs(
                parent_descriptor=output_chain.descriptor,
                temporary_archive=temporary_archive,
                temporary_manifest=temporary_manifest,
                archive=archive_path.name,
                manifest=manifest_path.name,
            )
            succeeded = True
            return {
                "schema": BACKUP_MANIFEST_SCHEMA,
                "status": "created",
                "archive": str(archive_path),
                "manifest": str(manifest_path),
                "file_count": len(records),
                "secret_scan": "passed",
            }
    finally:
        os.close(root_descriptor)
        if output_chain is not None:
            for name in (temporary_archive, temporary_manifest):
                if name is not None:
                    try:
                        os.unlink(name, dir_fd=output_chain.descriptor)
                    except FileNotFoundError:
                        pass
            if not succeeded:
                output_chain.cleanup_created()
            output_chain.close()


def create_runtime_backup(
    root: Path,
    *,
    output: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        observed_at = now or datetime.now(UTC).replace(microsecond=0)
        root_path = _absolute_path(root)
        archive_path, manifest_path = _resolve_backup_destinations(
            output,
            observed_at=observed_at,
        )
        _reject_in_source_destinations(
            root_path,
            archive=archive_path,
            manifest=manifest_path,
        )
        return _create_runtime_backup(
            root_path,
            archive_path=archive_path,
            manifest_path=manifest_path,
            observed_at=observed_at,
        )
    except RExecOpValidationError:
        raise
    except Exception as exc:
        raise RExecOpValidationError("runtime backup creation failed") from exc


def _open_regular_at(
    parent_descriptor: int,
    name: str,
    *,
    maximum_bytes: int,
    error: str,
) -> tuple[int, tuple[int, int, int, int, int, int]]:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum_bytes:
            raise RExecOpValidationError(error)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, _metadata_identity(metadata)


def _require_archive_identity(
    descriptor: int,
    opening_identity: tuple[int, int, int, int, int, int],
) -> None:
    metadata = os.fstat(descriptor)
    if metadata.st_size > _BACKUP_ARCHIVE_MAX_BYTES:
        raise RExecOpValidationError("backup archive exceeds size limit")
    if not stat.S_ISREG(metadata.st_mode) or _metadata_identity(metadata) != opening_identity:
        raise RExecOpValidationError("backup archive changed during restore")


def _read_bounded_regular_descriptor(
    descriptor: int,
    *,
    maximum_bytes: int,
    error: str,
) -> bytes:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum_bytes:
        raise RExecOpValidationError(error)
    os.lseek(descriptor, 0, os.SEEK_SET)
    data = bytearray()
    while len(data) <= maximum_bytes:
        chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
    if len(data) > maximum_bytes:
        raise RExecOpValidationError(error)
    return bytes(data)


def restore_runtime_backup(
    *,
    archive: Path,
    target_root: Path,
    manifest: Path | None = None,
) -> dict[str, Any]:
    archive_path = _absolute_path(archive)
    manifest_path = _absolute_path(manifest or archive.with_suffix(".manifest.json"))
    archive_chain: _DirectoryChain | None = None
    manifest_chain: _DirectoryChain | None = None
    target_chain: _DirectoryChain | None = None
    archive_descriptor: int | None = None
    stage_descriptor: int | None = None
    stage_name: str | None = None
    promoted = False
    try:
        archive_chain = _open_directory_chain(
            archive_path.parent,
            create=False,
            error=f"backup archive not found: {archive}",
        )
        try:
            archive_descriptor, archive_identity = _open_regular_at(
                archive_chain.descriptor,
                archive_path.name,
                maximum_bytes=_BACKUP_ARCHIVE_MAX_BYTES,
                error="backup archive exceeds size limit",
            )
        except OSError as exc:
            raise RExecOpValidationError(f"backup archive not found: {archive}") from exc

        if manifest_path.parent == archive_path.parent:
            manifest_parent = archive_chain
        else:
            manifest_chain = _open_directory_chain(
                manifest_path.parent,
                create=False,
                error="backup manifest is required for restore",
            )
            manifest_parent = manifest_chain
        try:
            manifest_descriptor, _ = _open_regular_at(
                manifest_parent.descriptor,
                manifest_path.name,
                maximum_bytes=_BACKUP_SIDECAR_MAX_BYTES,
                error="backup manifest exceeds size limit or is not a regular file",
            )
        except FileNotFoundError:
            if manifest_chain is not None:
                manifest_chain.close()
                manifest_chain = None
            manifest_path = archive_path.parent / MANIFEST_NAME
            try:
                manifest_descriptor, _ = _open_regular_at(
                    archive_chain.descriptor,
                    manifest_path.name,
                    maximum_bytes=_BACKUP_SIDECAR_MAX_BYTES,
                    error="backup manifest exceeds size limit or is not a regular file",
                )
            except OSError as exc:
                raise RExecOpValidationError("backup manifest is required for restore") from exc
        except OSError as exc:
            raise RExecOpValidationError("backup manifest is required for restore") from exc
        try:
            manifest_data = _read_bounded_regular_descriptor(
                manifest_descriptor,
                maximum_bytes=_BACKUP_SIDECAR_MAX_BYTES,
                error="backup manifest exceeds size limit or is not a regular file",
            )
        finally:
            os.close(manifest_descriptor)
        if manifest_chain is not None:
            manifest_chain.close()
            manifest_chain = None
        expected_files = _load_backup_manifest(
            manifest_data,
            archive_name=archive_path.name,
        )

        restore_target = _absolute_path(target_root)
        if restore_target == Path("/"):
            raise RExecOpValidationError("restore target parent is invalid")
        with os.fdopen(archive_descriptor, "rb") as archive_stream:
            archive_descriptor = None
            _validate_raw_ustar(archive_stream)
            archive_stream.seek(0)
            with tarfile.open(fileobj=archive_stream, mode="r:") as archive_handle:
                members = _validate_archive(archive_handle, expected_files)
                _require_archive_identity(archive_stream.fileno(), archive_identity)
                target_chain = _open_directory_chain(
                    restore_target.parent,
                    create=True,
                    error="restore target ancestors must be real directories",
                )
                target_before = _target_snapshot_at(
                    target_chain.descriptor,
                    restore_target.name,
                )
                stage_descriptor, stage_name = _temporary_directory(
                    target_chain.descriptor,
                    prefix=f".{restore_target.name}.restore-",
                )
                stage_identity = _directory_descriptor_identity(stage_descriptor)
                _extract_validated_members(
                    archive_handle,
                    members=members,
                    expected_files=expected_files,
                    stage_descriptor=stage_descriptor,
                )
                _require_archive_identity(archive_stream.fileno(), archive_identity)
                _revalidate_directory_chain(
                    target_chain,
                    error="restore target changed during restore",
                )
                if (
                    _directory_descriptor_identity(stage_descriptor) != stage_identity
                    or _directory_entry_identity(
                        target_chain.descriptor,
                        stage_name,
                        error="restore staging directory changed",
                    )
                    != stage_identity
                ):
                    raise RExecOpValidationError("restore staging directory changed")
                if (
                    _target_snapshot_at(target_chain.descriptor, restore_target.name)
                    != target_before
                ):
                    raise RExecOpValidationError("restore target changed during restore")
                try:
                    os.replace(
                        stage_name,
                        restore_target.name,
                        src_dir_fd=target_chain.descriptor,
                        dst_dir_fd=target_chain.descriptor,
                    )
                except OSError as exc:
                    raise RExecOpValidationError("backup restore promotion failed") from exc
                promoted = True
    except RExecOpValidationError:
        raise
    except (EOFError, tarfile.TarError) as exc:
        raise RExecOpValidationError("backup archive is invalid") from exc
    except OSError as exc:
        raise RExecOpValidationError("backup restore filesystem operation failed") from exc
    finally:
        if archive_descriptor is not None:
            os.close(archive_descriptor)
        if stage_descriptor is not None:
            os.close(stage_descriptor)
        if target_chain is not None:
            if stage_name is not None and not promoted:
                try:
                    shutil.rmtree(stage_name, dir_fd=target_chain.descriptor)
                except OSError:
                    pass
            if not promoted:
                target_chain.cleanup_created()
            target_chain.close()
        if manifest_chain is not None:
            manifest_chain.close()
        if archive_chain is not None:
            archive_chain.close()

    return {
        "schema": BACKUP_MANIFEST_SCHEMA,
        "status": "restored",
        "target_root": str(target_root),
        "file_count": len(expected_files),
        "manifest": str(manifest_path),
    }


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_bounded_regular_path(
    path: Path,
    *,
    maximum_bytes: int,
    error: str,
) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum_bytes:
                raise RExecOpValidationError(error)
            data = handle.read(maximum_bytes + 1)
    except RExecOpValidationError:
        raise
    except OSError as exc:
        raise RExecOpValidationError(error) from exc
    if len(data) > maximum_bytes:
        raise RExecOpValidationError(error)
    return data


def _load_backup_manifest(data: bytes, *, archive_name: str) -> dict[str, str]:
    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise RExecOpValidationError("backup manifest is invalid") from exc
    if not isinstance(payload, dict):
        raise RExecOpValidationError("backup manifest is invalid")
    if payload.get("schema") != BACKUP_MANIFEST_SCHEMA:
        raise RExecOpValidationError("unsupported backup manifest schema")
    if set(payload) != _BACKUP_MANIFEST_KEYS:
        raise RExecOpValidationError("backup manifest is invalid")
    version = payload.get("rexecop_version")
    fingerprint = payload.get("runtime_root_fingerprint")
    created_at = payload.get("created_at")
    declared_archive_name = payload.get("archive")
    file_count = payload.get("file_count")
    files = payload.get("files")
    if (
        not isinstance(version, str)
        or not version
        or version.strip() != version
        or not isinstance(fingerprint, str)
        or _FINGERPRINT.fullmatch(fingerprint) is None
        or not isinstance(created_at, str)
        or not created_at
        or not isinstance(declared_archive_name, str)
        or declared_archive_name != archive_name
        or "/" in declared_archive_name
        or "\\" in declared_archive_name
        or not isinstance(file_count, int)
        or isinstance(file_count, bool)
        or file_count < 0
        or file_count > _BACKUP_MAX_FILES
        or not isinstance(files, list)
        or file_count != len(files)
    ):
        raise RExecOpValidationError("backup manifest is invalid")
    try:
        datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise RExecOpValidationError("backup manifest is invalid") from exc

    expected_files: dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict) or set(item) != _BACKUP_FILE_KEYS:
            raise RExecOpValidationError("backup manifest is invalid")
        name = _validated_member_name(item.get("path"))
        digest = item.get("sha256")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise RExecOpValidationError("backup manifest is invalid")
        if name in expected_files:
            raise RExecOpValidationError("backup manifest contains duplicate paths")
        expected_files[name] = digest
    _validate_path_tree(expected_files)
    if len(expected_files) != file_count:
        raise RExecOpValidationError("backup manifest file count mismatch")
    if RUNTIME_MANIFEST not in expected_files:
        raise RExecOpValidationError("backup runtime manifest is required")
    return expected_files


def _portable_path_parts(value: str) -> tuple[str, ...]:
    portable: list[str] = []
    for part in PurePosixPath(value).parts:
        if part.endswith((".", " ")) or ":" in part:
            raise RExecOpValidationError("backup member path is invalid")
        canonical = unicodedata.normalize("NFC", part).casefold()
        basename = canonical.split(".", 1)[0]
        if basename in _WINDOWS_RESERVED_BASENAMES:
            raise RExecOpValidationError("backup member path is invalid")
        portable.append(canonical)
    return tuple(portable)


def _require_ustar_name(value: str) -> None:
    try:
        member = tarfile.TarInfo(value)
        member.tobuf(
            format=tarfile.USTAR_FORMAT,
            encoding="utf-8",
            errors="strict",
        )
    except (UnicodeError, ValueError) as exc:
        raise RExecOpValidationError("backup member path is not USTAR-representable") from exc


def _validated_member_name(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise RExecOpValidationError("backup member path is invalid")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RExecOpValidationError("backup member path is invalid") from exc
    if (
        len(encoded) > _MEMBER_NAME_MAX_BYTES
        or unicodedata.normalize("NFC", value) != value
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise RExecOpValidationError("backup member path is invalid")
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        path.is_absolute()
        or bool(windows_path.drive)
        or path.as_posix() != value
        or value == "."
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(len(part.encode("utf-8")) > _MEMBER_PART_MAX_BYTES for part in path.parts)
    ):
        raise RExecOpValidationError("backup member path is invalid")
    _portable_path_parts(value)
    _require_ustar_name(value)
    return value


def _validate_path_tree(names: Iterable[str]) -> None:
    portable_paths: dict[tuple[str, ...], str] = {}
    for name in names:
        path_parts = _portable_path_parts(name)
        if path_parts in portable_paths:
            raise RExecOpValidationError("backup paths contain a portable path collision")
        portable_paths[path_parts] = name
    parts = set(portable_paths)
    for path_parts in parts:
        if any(path_parts[:index] in parts for index in range(1, len(path_parts))):
            raise RExecOpValidationError("backup paths contain a path tree collision")


def _parse_ustar_size(field: bytes) -> int:
    value = field.rstrip(b"\0 ")
    if not value:
        return 0
    if any(character not in b"01234567" for character in value):
        raise RExecOpValidationError("backup archive is not strict USTAR")
    return int(value, 8)


def _parse_ustar_checksum(field: bytes) -> int:
    if (
        len(field) != 8
        or field[6:] != b"\0 "
        or any(character not in b"01234567" for character in field[:6])
    ):
        raise RExecOpValidationError("backup archive checksum is not canonical USTAR")
    return int(field[:6], 8)


def _validate_raw_ustar(archive_stream: BinaryIO) -> None:
    archive_stream.seek(0)
    members = 0
    total_size = 0
    zero_blocks = 0
    bytes_read = 0

    def read_bounded(size: int) -> bytes:
        nonlocal bytes_read
        remaining = _BACKUP_ARCHIVE_MAX_BYTES - bytes_read
        data = archive_stream.read(min(size, remaining + 1))
        bytes_read += len(data)
        if bytes_read > _BACKUP_ARCHIVE_MAX_BYTES:
            raise RExecOpValidationError("backup archive exceeds size limit")
        return data

    while True:
        header = read_bounded(tarfile.BLOCKSIZE)
        if not header:
            if zero_blocks < 2:
                raise RExecOpValidationError("backup archive is not strict USTAR")
            return
        if len(header) != tarfile.BLOCKSIZE:
            raise RExecOpValidationError("backup archive is not strict USTAR")
        if not any(header):
            zero_blocks += 1
            if zero_blocks >= 2:
                for trailing in iter(lambda: read_bounded(65536), b""):
                    if any(trailing):
                        raise RExecOpValidationError(
                            "backup archive contains non-zero trailing data"
                        )
                return
            continue
        if zero_blocks:
            raise RExecOpValidationError("backup archive is not strict USTAR")
        member_type = header[156:157]
        if member_type == tarfile.XGLTYPE:
            raise RExecOpValidationError("backup archive contains extension metadata")
        if member_type != tarfile.REGTYPE:
            raise RExecOpValidationError("backup archive contains non-regular member")
        if header[257:263] != b"ustar\0" or header[263:265] != b"00":
            raise RExecOpValidationError("backup archive is not strict USTAR")
        expected_checksum = _parse_ustar_checksum(header[148:156])
        actual_checksum = sum(header[:148]) + (8 * ord(" ")) + sum(header[156:])
        if actual_checksum != expected_checksum:
            raise RExecOpValidationError("backup archive checksum mismatch")
        members += 1
        if members > _BACKUP_MAX_FILES:
            raise RExecOpValidationError("backup archive member count exceeds limit")
        size = _parse_ustar_size(header[124:136])
        if size > _BACKUP_MEMBER_MAX_BYTES:
            raise RExecOpValidationError("backup archive member exceeds size limit")
        total_size += size
        if total_size > _BACKUP_TOTAL_MAX_BYTES:
            raise RExecOpValidationError("backup archive expanded size exceeds limit")
        remaining = size
        while remaining:
            chunk = read_bounded(min(65536, remaining))
            if not chunk:
                raise RExecOpValidationError("backup archive is invalid")
            remaining -= len(chunk)
        padding = (-size) % tarfile.BLOCKSIZE
        while padding:
            chunk = read_bounded(min(65536, padding))
            if not chunk:
                raise RExecOpValidationError("backup archive is invalid")
            if any(chunk):
                raise RExecOpValidationError("backup archive contains non-zero member padding")
            padding -= len(chunk)


def _validate_archive(
    archive_handle: tarfile.TarFile,
    expected_files: dict[str, str],
) -> list[tarfile.TarInfo]:
    if archive_handle.pax_headers:
        raise RExecOpValidationError("backup archive contains extension metadata")
    members: list[tarfile.TarInfo] = []
    names: set[str] = set()
    total_size = 0
    for member in archive_handle:
        if len(members) >= _BACKUP_MAX_FILES:
            raise RExecOpValidationError("backup archive member count exceeds limit")
        if (
            member.type != tarfile.REGTYPE
            or member.pax_headers
            or member.sparse is not None
            or member.offset_data != member.offset + tarfile.BLOCKSIZE
        ):
            raise RExecOpValidationError("backup archive contains non-regular member")
        name = _validated_member_name(member.name)
        if name in names:
            raise RExecOpValidationError("backup archive contains duplicate paths")
        if member.size < 0 or member.size > _BACKUP_MEMBER_MAX_BYTES:
            raise RExecOpValidationError("backup archive member exceeds size limit")
        total_size += member.size
        if total_size > _BACKUP_TOTAL_MAX_BYTES:
            raise RExecOpValidationError("backup archive expanded size exceeds limit")
        names.add(name)
        members.append(member)
    if archive_handle.pax_headers:
        raise RExecOpValidationError("backup archive contains extension metadata")
    _validate_path_tree(names)
    if len(members) != len(expected_files):
        raise RExecOpValidationError("backup archive member set mismatch")
    if names != set(expected_files):
        raise RExecOpValidationError("backup archive member set mismatch")

    runtime_manifest: bytes | None = None
    for member in members:
        capture = member.name == RUNTIME_MANIFEST
        digest, data = _digest_archive_member(
            archive_handle,
            member,
            capture=capture,
        )
        if digest != expected_files[member.name]:
            raise RExecOpValidationError("backup digest mismatch")
        if capture:
            runtime_manifest = data
    if runtime_manifest is None:
        raise RExecOpValidationError("backup runtime manifest is required")
    _validate_runtime_manifest(runtime_manifest)
    return members


def _digest_archive_member(
    archive_handle: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    capture: bool,
) -> tuple[str, bytes | None]:
    extracted = archive_handle.extractfile(member)
    if extracted is None:
        raise RExecOpValidationError("backup archive member is unreadable")
    digest = hashlib.sha256()
    size = 0
    captured = bytearray() if capture else None
    with extracted:
        for chunk in iter(lambda: extracted.read(65536), b""):
            size += len(chunk)
            digest.update(chunk)
            if captured is not None:
                if size > _RUNTIME_MANIFEST_MAX_BYTES:
                    raise RExecOpValidationError("backup runtime manifest is invalid")
                captured.extend(chunk)
    if size != member.size:
        raise RExecOpValidationError("backup archive member is truncated")
    return digest.hexdigest(), bytes(captured) if captured is not None else None


def _validate_runtime_manifest(data: bytes) -> None:
    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeError, ValueError) as exc:
        raise RExecOpValidationError("backup runtime manifest is invalid") from exc
    if not isinstance(payload, dict):
        raise RExecOpValidationError("backup runtime manifest is invalid")
    require_runtime_root_compatible(payload, target_version=__version__)


def _extract_validated_members(
    archive_handle: tarfile.TarFile,
    *,
    members: list[tarfile.TarInfo],
    expected_files: dict[str, str],
    stage_descriptor: int,
) -> None:
    for member in members:
        parts = PurePosixPath(member.name).parts
        parent_descriptor = _open_relative_directory(
            stage_descriptor,
            parts[:-1],
        )
        try:
            extracted = archive_handle.extractfile(member)
            if extracted is None:
                raise RExecOpValidationError("backup archive member is unreadable")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            digest = hashlib.sha256()
            size = 0
            try:
                descriptor = os.open(parts[-1], flags, 0o600, dir_fd=parent_descriptor)
                with os.fdopen(descriptor, "wb") as output, extracted:
                    os.fchmod(output.fileno(), 0o600)
                    for chunk in iter(lambda: extracted.read(65536), b""):
                        size += len(chunk)
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            except OSError as exc:
                raise RExecOpValidationError("backup restore staging failed") from exc
        finally:
            os.close(parent_descriptor)
        if size != member.size:
            raise RExecOpValidationError("backup archive member is truncated")
        if digest.hexdigest() != expected_files[member.name]:
            raise RExecOpValidationError("backup archive changed during restore")


def _open_relative_directory(root_descriptor: int, parts: tuple[str, ...]) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts:
            created = False
            try:
                os.mkdir(part, 0o700, dir_fd=descriptor)
                created = True
            except FileExistsError:
                pass
            child = os.open(part, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            if created:
                _chmod_directory(descriptor)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _temporary_directory(parent_descriptor: int, *, prefix: str) -> tuple[int, str]:
    for _ in range(100):
        name = f"{prefix}{secrets.token_hex(8)}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        descriptor: int | None = None
        try:
            descriptor = os.open(name, _directory_flags(), dir_fd=parent_descriptor)
            _chmod_directory(descriptor)
        except Exception:
            if descriptor is not None:
                os.close(descriptor)
            os.rmdir(name, dir_fd=parent_descriptor)
            raise
        assert descriptor is not None
        return descriptor, name
    raise RExecOpValidationError("unable to allocate restore staging directory")


def _directory_descriptor_identity(descriptor: int) -> tuple[int, int]:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise RExecOpValidationError("restore staging directory changed")
    return (metadata.st_dev, metadata.st_ino)


def _directory_entry_identity(
    parent_descriptor: int,
    name: str,
    *,
    error: str,
) -> tuple[int, int]:
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_descriptor)
    except OSError as exc:
        raise RExecOpValidationError(error) from exc
    try:
        return _directory_descriptor_identity(descriptor)
    finally:
        os.close(descriptor)


def _target_snapshot_at(
    parent_descriptor: int,
    name: str,
) -> tuple[
    str,
    tuple[int, int, int, int, int, int] | None,
]:
    try:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return ("absent", None)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RExecOpValidationError("restore target must be a real directory")
    descriptor = os.open(name, _directory_flags(), dir_fd=parent_descriptor)
    try:
        opened = os.fstat(descriptor)
        if _metadata_identity(opened) != _metadata_identity(metadata):
            raise RExecOpValidationError("restore target changed during restore")
        if os.listdir(descriptor):
            raise RExecOpValidationError("restore requires an absent or empty target")
        return ("empty", _metadata_identity(opened))
    finally:
        os.close(descriptor)


def _backup_paths() -> list[str]:
    paths = [RUNTIME_MANIFEST]
    paths.extend(RUNTIME_DIRECTORIES)
    extras = ("reactions", "queue/run_now.json")
    for item in extras:
        if item not in paths:
            paths.append(item)
    return paths
