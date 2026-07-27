from __future__ import annotations

import os
import stat
import uuid
from collections.abc import Callable
from pathlib import Path

from rexecop.errors import RExecOpValidationError

INBOX_ITEM_QUARANTINE_ERROR = "inbox item quarantine failed"
_DESTINATION_MODE = 0o700
_FILE_MODE = 0o600
_MAX_TARGET_ATTEMPTS = 8


def prepare_inbox_destination(directory: Path) -> None:
    """Create or normalize a private, non-symlink inbox destination."""
    try:
        try:
            directory.mkdir(mode=_DESTINATION_MODE)
        except FileExistsError:
            pass
        descriptor, _ = _open_private_inbox_directory(
            directory,
            normalize_permissions=True,
        )
        os.close(descriptor)
    except (OSError, RExecOpValidationError):
        raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR) from None


def normalize_inbox_directory(directory: Path) -> bool:
    """Normalize an existing direct inbox without following a symlink."""
    try:
        descriptor, _ = _open_private_inbox_directory(
            directory,
            normalize_permissions=True,
        )
        os.close(descriptor)
    except FileNotFoundError:
        return False
    except (OSError, RExecOpValidationError):
        raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR) from None
    return True


def quarantine_inbox_item(
    source: Path,
    destination_directory: Path,
    *,
    before_move: Callable[[Path], None] | None = None,
) -> Path:
    """Atomically move one direct inbox JSON item to a fresh bounded name."""
    reservation: Path | None = None
    reservation_created = False
    reservation_identity: tuple[int, int] | None = None
    source_directory_descriptor: int | None = None
    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    try:
        if not hasattr(os, "O_NOFOLLOW"):
            raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR)
        source_directory_descriptor, source_directory_identity = (
            _open_private_inbox_directory(
                source.parent,
                normalize_permissions=True,
            )
        )
        source_descriptor, source_identity = _open_regular_inbox_source(
            source,
            parent_descriptor=source_directory_descriptor,
        )
        destination_descriptor, destination_identity = _open_private_inbox_directory(
            destination_directory,
            normalize_permissions=False,
        )

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        for _ in range(_MAX_TARGET_ATTEMPTS):
            candidate = destination_directory / f"inbox-{uuid.uuid4().hex}.json"
            try:
                descriptor = os.open(
                    candidate.name,
                    flags,
                    _FILE_MODE,
                    dir_fd=destination_descriptor,
                )
            except FileExistsError:
                continue
            reservation = candidate
            reservation_created = True
            try:
                try:
                    owned = os.stat(descriptor)
                except Exception:
                    cleanup_metadata = os.fstat(descriptor)
                    reservation_identity = (
                        cleanup_metadata.st_dev,
                        cleanup_metadata.st_ino,
                    )
                    raise
                reservation_identity = (owned.st_dev, owned.st_ino)
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or (metadata.st_dev, metadata.st_ino) != reservation_identity
                ):
                    raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR)
                os.fchmod(descriptor, _FILE_MODE)
            finally:
                os.close(descriptor)
            if before_move is not None:
                before_move(candidate)
            _revalidate_move_topology(
                source=source,
                source_descriptor=source_descriptor,
                source_identity=source_identity,
                source_directory_descriptor=source_directory_descriptor,
                source_directory_identity=source_directory_identity,
                destination_directory=destination_directory,
                destination_descriptor=destination_descriptor,
                destination_identity=destination_identity,
                reservation_name=candidate.name,
                reservation_identity=reservation_identity,
            )
            os.replace(
                source.name,
                candidate.name,
                src_dir_fd=source_directory_descriptor,
                dst_dir_fd=destination_descriptor,
            )
            reservation = None
            reservation_created = False
            reservation_identity = None
            moved = os.stat(
                candidate.name,
                dir_fd=destination_descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(moved.st_mode) or not stat.S_ISREG(moved.st_mode):
                raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR)
            return candidate
        raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR)
    except Exception as exc:
        if (
            reservation_created
            and reservation is not None
            and reservation_identity is not None
            and destination_descriptor is not None
        ):
            _remove_own_reservation(
                destination_descriptor,
                reservation.name,
                reservation_identity,
            )
        if (
            isinstance(exc, RExecOpValidationError)
            and str(exc) != INBOX_ITEM_QUARANTINE_ERROR
        ):
            raise
        raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR) from None
    finally:
        for open_descriptor in (
            source_descriptor,
            source_directory_descriptor,
            destination_descriptor,
        ):
            if open_descriptor is not None:
                try:
                    os.close(open_descriptor)
                except OSError:
                    pass


def read_inbox_item_text(source: Path) -> str:
    """Read one direct regular inbox item without following a symlink."""
    directory_descriptor: int | None = None
    try:
        directory_descriptor, _ = _open_private_inbox_directory(
            source.parent,
            normalize_permissions=False,
        )
        descriptor, _ = _open_regular_inbox_source(
            source,
            parent_descriptor=directory_descriptor,
        )
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, RExecOpValidationError, UnicodeError):
        raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR) from None
    finally:
        if directory_descriptor is not None:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass


def refresh_inbox_item(source: Path) -> None:
    """Refresh a retryable direct regular inbox item without following links."""
    directory_descriptor: int | None = None
    try:
        directory_descriptor, _ = _open_private_inbox_directory(
            source.parent,
            normalize_permissions=False,
        )
        descriptor, _ = _open_regular_inbox_source(
            source,
            parent_descriptor=directory_descriptor,
        )
        try:
            os.utime(descriptor)
        finally:
            os.close(descriptor)
    except (OSError, RExecOpValidationError):
        raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR) from None
    finally:
        if directory_descriptor is not None:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass


def _open_regular_inbox_source(
    source: Path,
    *,
    parent_descriptor: int,
) -> tuple[int, tuple[int, int]]:
    if source.parent.name != "inbox" or source.suffix != ".json":
        raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR)
    metadata = os.stat(
        source.name,
        dir_fd=parent_descriptor,
        follow_symlinks=False,
    )
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source.name, flags, dir_fd=parent_descriptor)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (metadata.st_dev, metadata.st_ino):
            raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR)
        os.fchmod(descriptor, _FILE_MODE)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, (opened.st_dev, opened.st_ino)


def _open_private_inbox_directory(
    directory: Path,
    *,
    normalize_permissions: bool,
) -> tuple[int, tuple[int, int]]:
    metadata = directory.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR)
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(directory, flags)
    try:
        opened = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino)
        if not stat.S_ISDIR(opened.st_mode) or identity != (
            metadata.st_dev,
            metadata.st_ino,
        ):
            raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR)
        if normalize_permissions:
            os.fchmod(descriptor, _DESTINATION_MODE)
        current = directory.lstat()
        if (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != identity
            or stat.S_IMODE(current.st_mode) != _DESTINATION_MODE
        ):
            raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, identity


def _revalidate_move_topology(
    *,
    source: Path,
    source_descriptor: int,
    source_identity: tuple[int, int],
    source_directory_descriptor: int,
    source_directory_identity: tuple[int, int],
    destination_directory: Path,
    destination_descriptor: int,
    destination_identity: tuple[int, int],
    reservation_name: str,
    reservation_identity: tuple[int, int],
) -> None:
    current_source_directory = source.parent.lstat()
    current_destination = destination_directory.lstat()
    opened_destination = os.fstat(destination_descriptor)
    if (
        stat.S_ISLNK(current_source_directory.st_mode)
        or not stat.S_ISDIR(current_source_directory.st_mode)
        or (
            current_source_directory.st_dev,
            current_source_directory.st_ino,
        )
        != source_directory_identity
        or stat.S_ISLNK(current_destination.st_mode)
        or not stat.S_ISDIR(current_destination.st_mode)
        or (current_destination.st_dev, current_destination.st_ino)
        != destination_identity
        or stat.S_IMODE(current_destination.st_mode) != _DESTINATION_MODE
        or (opened_destination.st_dev, opened_destination.st_ino)
        != destination_identity
    ):
        raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR)
    opened_source = os.fstat(source_descriptor)
    current_source = os.stat(
        source.name,
        dir_fd=source_directory_descriptor,
        follow_symlinks=False,
    )
    current_reservation = os.stat(
        reservation_name,
        dir_fd=destination_descriptor,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISREG(opened_source.st_mode)
        or not stat.S_ISREG(current_source.st_mode)
        or not stat.S_ISREG(current_reservation.st_mode)
        or (opened_source.st_dev, opened_source.st_ino) != source_identity
        or (current_source.st_dev, current_source.st_ino) != source_identity
        or (current_reservation.st_dev, current_reservation.st_ino)
        != reservation_identity
    ):
        raise RExecOpValidationError(INBOX_ITEM_QUARANTINE_ERROR)


def _remove_own_reservation(
    destination_descriptor: int,
    name: str,
    identity: tuple[int, int],
) -> None:
    try:
        metadata = os.stat(
            name,
            dir_fd=destination_descriptor,
            follow_symlinks=False,
        )
        if (metadata.st_dev, metadata.st_ino) == identity and stat.S_ISREG(metadata.st_mode):
            os.unlink(name, dir_fd=destination_descriptor)
    except OSError:
        pass
