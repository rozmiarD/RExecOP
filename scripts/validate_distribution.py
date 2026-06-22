#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tarfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

FORBIDDEN_PARTS = frozenset({".cursor"})
FORBIDDEN_CONTENT = (
    b"/home/" + b"probo/",
    b"\\Users\\" + b"probo\\",
)
MAX_MEMBER_BYTES = 5 * 1024 * 1024


def _check_member(name: str, data: bytes) -> list[str]:
    errors: list[str] = []
    if FORBIDDEN_PARTS.intersection(PurePosixPath(name).parts):
        errors.append(f"{name}:forbidden_distribution_path")
    if len(data) <= MAX_MEMBER_BYTES:
        for marker in FORBIDDEN_CONTENT:
            if marker in data:
                errors.append(f"{name}:local_operator_path")
    return errors


def validate_archive(path: Path) -> list[str]:
    errors: list[str] = []
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                errors.extend(_check_member(info.filename, archive.read(info)))
        return errors
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                handle = archive.extractfile(member)
                data = handle.read() if handle is not None else b""
                errors.extend(_check_member(member.name, data))
        return errors
    return [f"{path.name}:unsupported_distribution_format"]


def _archives(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        if path.is_dir():
            result.extend(sorted(path.glob("*.whl")))
            result.extend(sorted(path.glob("*.tar.gz")))
        else:
            result.append(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    archives = _archives(args.paths)
    if not archives:
        print("distribution_validation_failed:no_archives")
        return 1
    errors: list[str] = []
    for archive in archives:
        errors.extend(f"{archive.name}:{error}" for error in validate_archive(archive))
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"distribution_validation_ok:{len(archives)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
