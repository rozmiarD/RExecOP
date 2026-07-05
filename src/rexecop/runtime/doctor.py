from __future__ import annotations

import importlib.metadata as metadata
from pathlib import Path
from typing import Any

from rexecop import __version__
from rexecop.catalog.service import CatalogService
from rexecop.environment.loader import load_environment
from rexecop.environment.sanitize import validate_no_inline_secrets
from rexecop.errors import RExecOpError
from rexecop.profile.conformance import validate_profile_conformance
from rexecop.runtime.contract_compatibility import (
    DOCTOR_REPORT_SCHEMA,
    contract_versions_summary,
    evaluate_stack_contract_compatibility,
)
from rexecop.runtime.init import RUNTIME_DIRECTORIES, RUNTIME_MANIFEST
from rexecop.storage.factory import resolve_storage_backend

EXPECTED_GOVENGINE = "0.16.9"
EXPECTED_SCLITE = "1.0.8"

CHECK_PASSED = "passed"
CHECK_WARNING = "warning"
CHECK_BLOCKER = "blocker"


def run_runtime_doctor(
    root: Path,
    *,
    storage_backend: str | None = None,
    instance: str | None = None,
    profile: str | None = None,
    env_path: Path | None = None,
    catalog_path: Path | None = None,
) -> dict[str, Any]:
    profile_check = _check_profile(profile)
    expected_profile = str(
        (profile_check.get("details") or {}).get("profile") or ""
    )
    checks = [
        _check_runtime_root(root),
        _check_storage_backend(storage_backend),
        _check_runtime_layout(root),
        _check_stack_packages(),
        _check_typed_execution_stack_compatibility(),
        _check_stack_contract_compatibility(),
        profile_check,
        _check_environment(env_path, expected_profile=expected_profile),
        _check_catalog(catalog_path),
    ]
    blockers = [check["id"] for check in checks if check["status"] == CHECK_BLOCKER]
    warnings = [check["id"] for check in checks if check["status"] == CHECK_WARNING]
    status = CHECK_BLOCKER if blockers else CHECK_WARNING if warnings else CHECK_PASSED
    next_actions = [
        str(check["next_action"])
        for check in checks
        if check.get("next_action")
    ]
    profile_version = str(
        (profile_check.get("details") or {}).get("version") or ""
    )
    return {
        "schema": DOCTOR_REPORT_SCHEMA,
        "status": status,
        "root": str(root),
        "instance": instance,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "next_actions": sorted(set(next_actions)),
        "contract_versions": contract_versions_summary(profile_version=profile_version),
    }


def _check(
    check_id: str,
    status: str,
    summary: str,
    *,
    details: dict[str, Any] | None = None,
    next_action: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": check_id,
        "status": status,
        "summary": summary,
    }
    if details:
        payload["details"] = details
    if next_action:
        payload["next_action"] = next_action
    return payload


def _check_runtime_root(root: Path) -> dict[str, Any]:
    if not root.exists():
        return _check(
            "runtime_root",
            CHECK_BLOCKER,
            "runtime root does not exist",
            next_action=f"rexecop --root {root} init",
        )
    if not root.is_dir():
        return _check(
            "runtime_root",
            CHECK_BLOCKER,
            "runtime root is not a directory",
        )
    return _check("runtime_root", CHECK_PASSED, "runtime root exists")


def _check_storage_backend(storage_backend: str | None) -> dict[str, Any]:
    try:
        backend = resolve_storage_backend(storage_backend)
    except RExecOpError as exc:
        return _check("storage_backend", CHECK_BLOCKER, str(exc))
    return _check(
        "storage_backend",
        CHECK_PASSED,
        "storage backend is supported",
        details={"backend": backend},
    )


def _check_runtime_layout(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        return _check(
            "runtime_layout",
            CHECK_BLOCKER,
            "runtime layout cannot be checked before root exists",
            next_action=f"rexecop --root {root} init",
        )
    expected = [RUNTIME_MANIFEST, *RUNTIME_DIRECTORIES, "queue/run_now.json"]
    missing = sorted(relative for relative in expected if not (root / relative).exists())
    if missing:
        return _check(
            "runtime_layout",
            CHECK_WARNING,
            "runtime layout is incomplete",
            details={"missing": missing},
            next_action=f"rexecop --root {root} init",
        )
    return _check("runtime_layout", CHECK_PASSED, "runtime layout is initialized")


def _check_typed_execution_stack_compatibility() -> dict[str, Any]:
    try:
        from rexecop.execution.govengine_governance import (
            evaluate_typed_execution_stack_compatibility,
        )

        result = evaluate_typed_execution_stack_compatibility()
    except Exception as exc:  # noqa: BLE001 - doctor boundary
        return _check(
            "typed_execution_stack_compatibility",
            CHECK_BLOCKER,
            "typed execution stack compatibility check failed",
            details={"error": str(exc)},
            next_action="install compatible govengine and rerun rexecop doctor",
        )
    if result["status"] != "passed":
        return _check(
            "typed_execution_stack_compatibility",
            CHECK_BLOCKER,
            "GovEngine typed execution controls do not cover RExecOp backends",
            details={
                "unsupported_backends": result["unsupported_backends"],
                "missing_controls": result["missing_controls"],
                "blockers": result["blockers"],
            },
            next_action="align rexecop backend descriptors with govengine typed execution controls",
        )
    return _check(
        "typed_execution_stack_compatibility",
        CHECK_PASSED,
        "GovEngine typed execution controls cover RExecOp backend descriptors",
        details={
            "supported_backends": result["supported_backends"],
            "control_count": len(
                result["govengine_control_catalog"].get("controls") or []
            ),
        },
    )


def _check_stack_contract_compatibility() -> dict[str, Any]:
    try:
        result = evaluate_stack_contract_compatibility()
    except Exception as exc:  # noqa: BLE001 - doctor boundary
        return _check(
            "stack_contract_compatibility",
            CHECK_BLOCKER,
            "stack contract compatibility check failed",
            details={"error": str(exc)},
            next_action="install compatible govengine and rerun rexecop doctor",
        )
    if result["status"] != "passed":
        return _check(
            "stack_contract_compatibility",
            CHECK_BLOCKER,
            "stack contract compatibility failed",
            details={
                "blockers": result["blockers"],
                "govengine_blockers": result["govengine_contracts"].get("blockers"),
            },
            next_action=(
                "align rexecop/govengine/sclite contract pins with supported-contract report"
            ),
        )
    govengine = result["govengine_contracts"]
    return _check(
        "stack_contract_compatibility",
        CHECK_PASSED,
        "stack contract compatibility passed",
        details={
            "govengine_version": govengine["govengine_version"],
            "matched_contracts": govengine["matched_contracts"],
            "projection_count": len(result["runtime_projections"]["projections"]),
            "sclite_artifact_count": len(result["sclite_artifact_refs"]),
        },
    )


def _check_stack_packages() -> dict[str, Any]:
    found = {
        "rexecop": __version__,
        "govengine": _package_version("govengine"),
        "sclite-core": _package_version("sclite-core"),
    }
    mismatches: list[str] = []
    if found["govengine"] != EXPECTED_GOVENGINE:
        mismatches.append(f"govengine:{found['govengine']}!={EXPECTED_GOVENGINE}")
    if found["sclite-core"] != EXPECTED_SCLITE:
        mismatches.append(f"sclite-core:{found['sclite-core']}!={EXPECTED_SCLITE}")
    if mismatches:
        return _check(
            "stack_packages",
            CHECK_BLOCKER,
            "required governance/truth package versions do not match",
            details={"found": found, "mismatches": mismatches},
        )
    return _check(
        "stack_packages",
        CHECK_PASSED,
        "required governance/truth packages are compatible",
        details={"found": found},
    )


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not_installed"


def _check_profile(profile: str | None) -> dict[str, Any]:
    if profile is None or not str(profile).strip():
        return _check(
            "profile_conformance",
            CHECK_WARNING,
            "profile was not provided",
            next_action="rerun doctor with --profile when checking profile conformance",
        )
    try:
        result = validate_profile_conformance(
            profile,
            require_reaction_observation=False,
            track="readonly",
        )
    except RExecOpError as exc:
        return _check(
            "profile_conformance",
            CHECK_BLOCKER,
            str(exc),
            next_action=f"install or fix profile: {profile}",
        )
    if result.status != "passed":
        return _check(
            "profile_conformance",
            CHECK_BLOCKER,
            "profile conformance failed",
            details=result.as_dict(),
        )
    return _check(
        "profile_conformance",
        CHECK_PASSED,
        "profile readonly conformance passed",
        details={
            "profile": result.profile,
            "version": result.version,
            "track": result.track,
            "checked_intents": list(result.checked_intents),
            "mutation_candidate_intents": list(result.mutation_candidate_intents),
            "reaction_observation_intents": list(result.reaction_observation_intents),
        },
    )


def _check_environment(env_path: Path | None, *, expected_profile: str) -> dict[str, Any]:
    if env_path is None:
        return _check(
            "environment",
            CHECK_WARNING,
            "environment was not provided",
            next_action="rerun doctor with --env when checking a real operator environment",
        )
    try:
        environment = load_environment(env_path)
        validate_no_inline_secrets(environment.as_dict())
    except RExecOpError as exc:
        return _check("environment", CHECK_BLOCKER, str(exc))
    status = CHECK_PASSED
    summary = "environment is valid and contains no inline secrets"
    details = {
        "id": environment.id,
        "profile": environment.profile,
        "target_count": len(environment.targets),
        "connector_count": len(environment.connectors),
            "secret_ref_count": count_secret_refs(environment.as_dict()),
    }
    if expected_profile and environment.profile and environment.profile != expected_profile:
        status = CHECK_BLOCKER
        summary = "environment profile does not match doctor profile"
        details["expected_profile"] = expected_profile
    return _check("environment", status, summary, details=details)


def count_secret_refs(value: Any) -> int:
    if isinstance(value, dict):
        count = 0
        for key, item in value.items():
            if str(key).endswith("_secret_ref") or str(key) == "secret_ref":
                if str(item or "").strip():
                    count += 1
            count += count_secret_refs(item)
        return count
    if isinstance(value, list):
        return sum(count_secret_refs(item) for item in value)
    return 0


def _check_catalog(catalog_path: Path | None) -> dict[str, Any]:
    if catalog_path is None:
        return _check(
            "catalog",
            CHECK_WARNING,
            "target catalog was not provided",
            next_action="rerun doctor with --catalog when checking target applicability",
        )
    try:
        service = CatalogService(catalog_path)
        targets = service.list_targets()
    except RExecOpError as exc:
        return _check("catalog", CHECK_BLOCKER, str(exc))
    return _check(
        "catalog",
        CHECK_PASSED,
        "target catalog is valid",
        details={
            "catalog_version": service.version,
            "catalog_digest": service.digest,
            "target_count": len(targets),
        },
    )
