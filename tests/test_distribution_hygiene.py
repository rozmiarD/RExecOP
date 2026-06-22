from __future__ import annotations

import importlib.util
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_distribution.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("rexecop_validate_distribution", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_sdist(path: Path, source: Path, archive_name: str) -> None:
    with tarfile.open(path, "w:gz") as archive:
        archive.add(source, arcname=archive_name)


def test_distribution_validator_rejects_local_metadata(tmp_path: Path) -> None:
    validator = _load_validator()
    source = tmp_path / "rule.mdc"
    source.write_text("/home/" + "probo/private/token-wrapper")
    archive = tmp_path / "package.tar.gz"
    _write_sdist(archive, source, "package/.cursor/rule.mdc")
    errors = validator.validate_archive(archive)
    assert any("forbidden_distribution_path" in error for error in errors)
    assert any("local_operator_path" in error for error in errors)


def test_distribution_validator_accepts_clean_sdist(tmp_path: Path) -> None:
    validator = _load_validator()
    source = tmp_path / "README.md"
    source.write_text("clean package")
    archive = tmp_path / "package.tar.gz"
    _write_sdist(archive, source, "package/README.md")
    assert validator.validate_archive(archive) == []
