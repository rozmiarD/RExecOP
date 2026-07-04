from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rexecop import __version__
from rexecop.storage.atomic import atomic_write_text, secure_directory
from rexecop.storage.factory import create_store, resolve_storage_backend

RUNTIME_MANIFEST = "runtime_manifest.json"
INIT_SCHEMA = "rexecop.runtime_init.v0.1"
RUNTIME_DIRECTORIES = (
    "operations",
    "plans",
    "evidence",
    "receipts",
    "sclite",
    "approvals",
    "queue",
    "locks",
    "inbox",
    "dead_letter",
    "triggers",
    "watchdog",
    "watchdog/records",
    "watchdog/sclite",
)


def initialize_runtime_root(
    root: Path,
    *,
    backend: str | None = None,
    instance: str | None = None,
    guided: bool = False,
) -> dict[str, Any]:
    storage_backend = resolve_storage_backend(backend)
    store = create_store(root, backend=storage_backend)
    before = {path for path in _runtime_paths(root) if path.exists()}

    store.ensure_layout()
    for relative in RUNTIME_DIRECTORIES:
        secure_directory(root / relative)
    queue_file = root / "queue" / "run_now.json"
    if not queue_file.exists():
        atomic_write_text(queue_file, json.dumps({"pending": []}, indent=2) + "\n")

    manifest = {
        "schema": INIT_SCHEMA,
        "rexecop_version": __version__,
        "storage_backend": storage_backend,
        "runtime_root": str(root),
        "runtime_instance": instance,
        "initialized_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "secrets_created": False,
    }
    atomic_write_text(
        root / RUNTIME_MANIFEST,
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )

    after = {path for path in _runtime_paths(root) if path.exists()}
    created = sorted(str(path.relative_to(root)) for path in after - before)
    existing = sorted(str(path.relative_to(root)) for path in after & before)
    result: dict[str, Any] = {
        "status": "initialized",
        "root": str(root),
        "instance": instance,
        "storage_backend": storage_backend,
        "manifest": RUNTIME_MANIFEST,
        "created": created,
        "existing": existing,
        "secrets_created": False,
    }
    if guided:
        result["guided"] = True
        result["next_steps"] = [
            "rexecop doctor --profile <profile.yaml> --env <environment.yaml> "
            "--catalog <targets.yaml>",
            "rexecop profile lint --profile <profile.yaml> --track readonly",
            "rexecop env lint --env <environment.yaml> --profile <profile.yaml>",
            "rexecop operations explain <intent> --profile <profile.yaml>",
            "rexecop plan --profile <profile.yaml> --env <environment.yaml> "
            "--intent <intent> --target <target> --mode dry_run",
        ]
    return result


def _runtime_paths(root: Path) -> tuple[Path, ...]:
    return (
        root,
        *(root / relative for relative in RUNTIME_DIRECTORIES),
        root / "queue" / "run_now.json",
        root / RUNTIME_MANIFEST,
    )
