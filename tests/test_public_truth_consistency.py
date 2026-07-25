from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_public_truth.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("rexecop_validate_public_truth", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_public_truth_validator_passes() -> None:
    validator = _load_validator()
    version = validator.current_version()
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip().startswith(f"public_truth_ok:rexecop=={version}:")


def test_public_truth_rejects_package_version_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    validator = _load_validator()
    monkeypatch.setattr(validator.rexecop, "__version__", "9.9.9a0")
    errors = validator.collect_errors()
    assert any("package_version_mismatch" in item for item in errors)


def test_public_truth_rejects_reintroduced_tecrax_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    project = validator._pyproject()
    project.setdefault("optional-dependencies", {})["tecrax"] = ["tecrax==9.9.9"]
    monkeypatch.setattr(validator, "_pyproject", lambda: project)

    errors = validator.collect_errors()

    assert "tecrax_extra_must_not_ship_in_v1_core" in errors


def test_public_truth_rejects_stale_govengine_public_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    record = (ROOT / "docs/alpha-sign-off-record.md").read_text(encoding="utf-8")
    stale_record = record.replace(
        validator.EXPECTED_GOVENGINE_STATUS,
        "`1.0.0rc1` (source candidate; published `0.16.11`)",
    )

    def fake_read(path: str) -> str:
        if path == "docs/alpha-sign-off-record.md":
            return stale_record
        return (ROOT / path).read_text(encoding="utf-8")

    monkeypatch.setattr(validator, "_read", fake_read)
    errors = validator.collect_errors()
    assert any("docs/alpha-sign-off-record.md:missing:" in item for item in errors)
    assert any("docs/alpha-sign-off-record.md:forbidden:" in item for item in errors)


def test_public_truth_rejects_stale_readme_operator_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    version = validator.current_version()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    stale_readme = readme.replace(version, version).replace(
        f"| Current source line | `{version}`",
        "| Current source line | `0.1.2a0`",
    )

    def fake_read(path: str) -> str:
        if path == "README.md":
            return stale_readme
        return (ROOT / path).read_text(encoding="utf-8")

    monkeypatch.setattr(validator, "_read", fake_read)
    errors = validator.collect_errors()
    assert any("README.md:stale_operator_version:0.1.2a0" in item for item in errors)


def test_policy_import_is_independent_of_connector_import_order() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from rexecop.policy.enforcement import "
                "validate_policy_enforcement_record; "
                "from rexecop.connectors import CompositeConnectorRuntime; "
                "assert callable(validate_policy_enforcement_record); "
                "assert CompositeConnectorRuntime.__name__ == "
                "'CompositeConnectorRuntime'"
            ),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_public_truth_tracks_m3_m4_cli_markers() -> None:
    validator = _load_validator()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for marker in validator.M3_M4_CLI_MARKERS:
        assert marker in readme, marker


def test_public_truth_rejects_missing_m3_m4_cli_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    validator = _load_validator()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    stale_readme = readme.replace("secrets doctor", "secrets check")

    def fake_read(path: str) -> str:
        if path == "README.md":
            return stale_readme
        return (ROOT / path).read_text(encoding="utf-8")

    monkeypatch.setattr(validator, "_read", fake_read)
    errors = validator.collect_errors()
    assert any("README.md:missing:secrets doctor" in item for item in errors)


def test_public_truth_rejects_missing_changelog_section(monkeypatch: pytest.MonkeyPatch) -> None:
    validator = _load_validator()

    def fake_read(path: str) -> str:
        text = (ROOT / path).read_text(encoding="utf-8")
        if path == "CHANGELOG.md":
            return text.replace(f"## [{validator.current_version()}]", "## [0.0.0a0]")
        return text

    monkeypatch.setattr(validator, "_read", fake_read)
    errors = validator.collect_errors()
    assert any("CHANGELOG.md:missing_release_section" in item for item in errors)
