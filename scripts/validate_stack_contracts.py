#!/usr/bin/env python3
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import rexecop  # noqa: E402

EXPECTED_REXECOP = "0.2.12a0"
EXPECTED_GOVENGINE = "govengine==0.16.6"
EXPECTED_SCLITE = "sclite-core==1.0.8"
EXPECTED_TECRAX = "tecrax==0.3.9a0"

ACTIVE_READINESS = (
    "alpha_readonly",
    "deterministic_plan_only",
    "deterministic_execute_readonly",
)
NON_ACTIVE_READINESS = (
    "advisory_llm",
    "mutation_ready",
)

REQUIRED_DOC_MARKERS = (
    "sclite-core==1.0.8",
    "govengine==0.16.6",
    "rexecop` | `0.2.12a0`",
    "tecrax==0.3.9a0",
    "observation_envelope.v0.1",
    "PolicyEnforcementPlan",
    "ExecutionRequest` / `ExecutionReceipt` schema `v0.2`",
    "tecrax.monitoring_host_diagnosis@1.0",
    "`mutation_ready` | false",
    "not a `mutation_ready` claim",
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _pyproject() -> dict:
    return tomllib.loads(_read("pyproject.toml"))["project"]


def _dependency(project: dict, name: str) -> str:
    prefix = name
    for dependency in project.get("dependencies", []):
        text = str(dependency)
        if text.startswith(prefix):
            return text
    raise AssertionError(f"missing_dependency:{name}")


def _optional_extra(project: dict, extra: str) -> list[str]:
    optional = project.get("optional-dependencies") or {}
    items = optional.get(extra)
    if not isinstance(items, list):
        raise AssertionError(f"missing_optional_extra:{extra}")
    return [str(item) for item in items]


def _require(errors: list[str], path: str, text: str, marker: str) -> None:
    if marker not in text:
        errors.append(f"{path}:missing:{marker}")


def collect_errors() -> list[str]:
    errors: list[str] = []
    project = _pyproject()
    docs = {
        "docs/stack-contract-compatibility.md": _read("docs/stack-contract-compatibility.md"),
        "docs/known-limitations.md": _read("docs/known-limitations.md"),
        "README.md": _read("README.md"),
    }

    version = str(project["version"])
    if version != EXPECTED_REXECOP:
        errors.append(f"rexecop_version_mismatch:{version}!={EXPECTED_REXECOP}")
    if rexecop.__version__ != version:
        errors.append(f"package_version_mismatch:{rexecop.__version__}!={version}")
    if _dependency(project, "govengine") != EXPECTED_GOVENGINE:
        errors.append("govengine_dependency_mismatch")
    if _dependency(project, "sclite-core") != EXPECTED_SCLITE:
        errors.append("sclite_dependency_mismatch")
    if EXPECTED_TECRAX not in _optional_extra(project, "tecrax"):
        errors.append("tecrax_extra_mismatch")

    matrix = docs["docs/stack-contract-compatibility.md"]
    for marker in REQUIRED_DOC_MARKERS:
        _require(errors, "docs/stack-contract-compatibility.md", matrix, marker)
    for label in (*ACTIVE_READINESS, *NON_ACTIVE_READINESS):
        _require(errors, "docs/stack-contract-compatibility.md", matrix, f"`{label}`")
    _require(
        errors,
        "docs/known-limitations.md",
        docs["docs/known-limitations.md"],
        "Stack readiness labels",
    )
    _require(errors, "README.md", docs["README.md"], "docs/stack-contract-compatibility.md")

    if "`advisory_llm` | active" in matrix:
        errors.append("docs/stack-contract-compatibility.md:advisory_llm_must_not_be_active")
    if "`mutation_ready` | active" in matrix or "`mutation_ready` | true" in matrix:
        errors.append("docs/stack-contract-compatibility.md:mutation_ready_must_not_be_active")
    return errors


def main() -> int:
    errors = collect_errors()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(
        "stack_contracts_ok:"
        f"readiness={','.join(ACTIVE_READINESS)}:"
        f"blocked={','.join(NON_ACTIVE_READINESS)}:"
        f"rexecop=={EXPECTED_REXECOP}:"
        f"{EXPECTED_GOVENGINE}:{EXPECTED_SCLITE}:{EXPECTED_TECRAX}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
