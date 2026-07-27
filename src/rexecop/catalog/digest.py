from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from rexecop.errors import RExecOpValidationError, _InvalidJsonValue
from rexecop.yaml_input import ensure_finite_json_value, load_yaml_file


def canonical_digest(value: Any) -> str:
    ensure_finite_json_value(value)
    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _InvalidJsonValue() from exc
    return hashlib.sha256(rendered).hexdigest()


def yaml_document_digest(path: Path) -> str:
    value = load_yaml_file(path)
    return canonical_digest(value)


def text_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def profile_snapshot_digest(root: Path) -> str:
    snapshot: list[dict[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if "__pycache__" in path.parts or path.suffix not in {".yaml", ".yml", ".json"}:
            continue
        snapshot.append(
            {
                "path": path.relative_to(root).as_posix(),
                "digest": yaml_document_digest(path),
            }
        )
    if not snapshot:
        raise RExecOpValidationError(f"profile snapshot is empty: {root}")
    return canonical_digest(snapshot)
