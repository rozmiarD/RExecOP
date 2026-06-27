from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_stack_contracts.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("rexecop_validate_stack_contracts", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_stack_contract_validator_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.strip().startswith(
        "stack_contracts_ok:readiness=alpha_readonly,deterministic_plan_only,"
        "deterministic_execute_readonly:"
    )


def test_stack_contract_validator_rejects_mutation_ready_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()

    def fake_read(path: str) -> str:
        text = (ROOT / path).read_text(encoding="utf-8")
        if path == "docs/stack-contract-compatibility.md":
            return text.replace("`mutation_ready` | false", "`mutation_ready` | true")
        return text

    monkeypatch.setattr(validator, "_read", fake_read)
    errors = validator.collect_errors()
    assert any("mutation_ready_must_not_be_active" in item for item in errors)


def test_stack_contract_validator_rejects_dependency_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    project = validator._pyproject()
    project["dependencies"] = [
        "typer>=0.12.0",
        "PyYAML>=6.0",
        "govengine>=0.15.0,<0.16",
        "sclite-core>=1.0.5,<1.1",
    ]

    monkeypatch.setattr(validator, "_pyproject", lambda: project)
    errors = validator.collect_errors()
    assert "govengine_dependency_mismatch" in errors
