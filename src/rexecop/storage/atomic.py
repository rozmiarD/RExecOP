from __future__ import annotations

import os
from pathlib import Path

DIRECTORY_MODE = 0o700
FILE_MODE = 0o600


def secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    path.chmod(DIRECTORY_MODE)


def secure_file(path: Path) -> None:
    path.chmod(FILE_MODE)


def secure_tree(path: Path) -> None:
    secure_directory(path)
    for entry in path.rglob("*"):
        if entry.is_dir():
            entry.chmod(DIRECTORY_MODE)
        elif entry.is_file():
            entry.chmod(FILE_MODE)


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write UTF-8 text via a sibling temp file and atomic replace."""
    secure_directory(path.parent)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(
            tmp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            FILE_MODE,
        )
        with os.fdopen(descriptor, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        secure_file(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
