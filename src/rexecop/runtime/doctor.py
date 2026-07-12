from __future__ import annotations

import importlib.metadata as metadata
import os
from pathlib import Path
from typing import Any

from rexecop import __version__
from rexecop.catalog.service import CatalogService
from rexecop.connectors.http_support import destination_binding
from rexecop.environment.loader import load_environment
from rexecop.environment.sanitize import validate_no_inline_secrets
from rexecop.errors import RExecOpError
from rexecop.profile.conformance import validate_profile_conformance
from rexecop.profile.extension_manifest import build_plugin_compatibility_report
from rexecop.runtime.contract_compatibility import (
    DOCTOR_REPORT_SCHEMA,
    contract_versions_summary,
    evaluate_stack_contract_compatibility,
)
from rexecop.runtime.init import RUNTIME_DIRECTORIES, RUNTIME_MANIFEST
from rexecop.storage.factory import resolve_storage_backend

EXPECTED_GOVENGINE = "0.17.0rc2"
EXPECTED_SCLITE = "2.0.0"

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
    executor_posture: str | None = None,
    deployment_posture: str | None = None,
    plugin_allowlist: str | None = None,
) -> dict[str, Any]:
    profile_check = _check_profile(profile)
    expected_profile = str((profile_check.get("details") or {}).get("profile") or "")
    storage_check = _check_storage_backend(storage_backend)
    checks = [
        _check_runtime_root(root),
        storage_check,
        _check_executor_posture(executor_posture or os.environ.get("REXECOP_EXECUTOR_POSTURE")),
        _check_plugin_posture(
            deployment_posture or os.environ.get("REXECOP_DEPLOYMENT_POSTURE") or "alpha",
            plugin_allowlist
            if plugin_allowlist is not None
            else os.environ.get("REXECOP_PLUGIN_ALLOWLIST"),
        ),
        _check_runtime_layout(root),
        _check_stack_packages(),
        _check_typed_execution_stack_compatibility(),
        _check_stack_contract_compatibility(),
        profile_check,
        _check_environment(env_path, expected_profile=expected_profile),
        _check_network_egress_posture(env_path),
        _check_catalog(catalog_path),
    ]
    blockers = [check["id"] for check in checks if check["status"] == CHECK_BLOCKER]
    warnings = [check["id"] for check in checks if check["status"] == CHECK_WARNING]
    status = CHECK_BLOCKER if blockers else CHECK_WARNING if warnings else CHECK_PASSED
    next_actions = [str(check["next_action"]) for check in checks if check.get("next_action")]
    profile_version = str((profile_check.get("details") or {}).get("version") or "")
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
        CHECK_PASSED if backend == "file" else CHECK_BLOCKER,
        (
            "storage backend is certified for stable single-host execution"
            if backend == "file"
            else "storage backend is supported but not stable-runtime certified"
        ),
        details={
            "backend": backend,
            "certification_tier": (
                "stable_single_host" if backend == "file" else "alpha_single_host"
            ),
            "single_executor": backend == "file",
            "multi_executor": False,
        },
        next_action=(
            "set REXECOP_STORAGE=file for stable single-host execution" if backend != "file" else ""
        ),
    )


def _check_executor_posture(value: str | None) -> dict[str, Any]:
    posture = (value or "single_executor").strip().lower()
    if posture != "single_executor":
        return _check(
            "executor_posture",
            CHECK_BLOCKER,
            "only one active executor per runtime root is certified",
            details={
                "requested": posture,
                "certified": "single_executor",
                "distributed_or_multi_worker": False,
            },
            next_action="set REXECOP_EXECUTOR_POSTURE=single_executor",
        )
    return _check(
        "executor_posture",
        CHECK_PASSED,
        "single-executor runtime posture is certified",
        details={
            "requested": posture,
            "certified": "single_executor",
            "lease_scope": "one runtime root",
        },
    )


def _check_plugin_posture(
    deployment_posture: str,
    allowlist_value: str | None,
) -> dict[str, Any]:
    posture = deployment_posture.strip().lower()
    report = build_plugin_compatibility_report()
    inventory = report.get("inventory") or {}
    connector_plugins = [
        str(item.get("name") or "") for item in inventory.get("connector_backends") or []
    ]
    internal_plugins = [
        str(item.get("name") or "") for item in inventory.get("internal_action_registrars") or []
    ]
    installed = sorted(set(connector_plugins + internal_plugins) - {""})
    allowlist = sorted(
        {item.strip() for item in (allowlist_value or "").split(",") if item.strip()}
    )
    unallowed = sorted(set(installed) - set(allowlist))
    compatibility_failures = list(report.get("failed") or [])
    blockers = list(compatibility_failures)
    if posture == "stable":
        blockers.extend(unallowed)
    if blockers:
        return _check(
            "plugin_posture",
            CHECK_BLOCKER,
            "plugin inventory is incompatible with the requested deployment posture",
            details={
                "deployment_posture": posture,
                "execution_model": "trusted_in_process",
                "installed": installed,
                "allowlist": allowlist,
                "unallowed": unallowed,
                "compatibility_failures": compatibility_failures,
            },
            next_action="set REXECOP_PLUGIN_ALLOWLIST to reviewed plugin entry-point names",
        )
    return _check(
        "plugin_posture",
        CHECK_PASSED,
        "trusted in-process plugin inventory is compatible",
        details={
            "deployment_posture": posture,
            "execution_model": "trusted_in_process",
            "sandboxed": False,
            "installed": installed,
            "allowlist": allowlist,
        },
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
            "control_count": len(result["govengine_control_catalog"].get("controls") or []),
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


def _check_network_egress_posture(env_path: Path | None) -> dict[str, Any]:
    if env_path is None:
        return _check(
            "network_egress_posture",
            CHECK_WARNING,
            "network posture was not evaluated without an environment",
            next_action="rerun doctor with --env to evaluate connector egress posture",
        )
    try:
        environment = load_environment(env_path)
    except RExecOpError as exc:
        return _check("network_egress_posture", CHECK_BLOCKER, str(exc))
    blockers: list[str] = []
    checked: list[dict[str, Any]] = []
    for name, raw in environment.connectors.items():
        config = dict(raw) if isinstance(raw, dict) else {}
        if str(config.get("backend") or "") != "http_api":
            continue
        posture = str(config.get("deployment_posture") or "stable").strip().lower()
        binding_value = config.get("destination_binding")
        base_url = str(config.get("base_url") or "").strip()
        binding: dict[str, Any] = {}
        if base_url:
            try:
                binding = destination_binding(base_url)
            except ValueError:
                blockers.append(f"{name}:unsafe_destination")
        elif isinstance(binding_value, dict):
            binding = dict(binding_value)
        elif posture == "stable":
            blockers.append(f"{name}:destination_binding_missing")
        if posture not in {"stable", "lab", "fixture"}:
            blockers.append(f"{name}:deployment_posture_invalid")
        if binding:
            digest = str(binding.get("origin_binding_digest") or "")
            if (
                str(binding.get("scheme") or "") not in {"http", "https"}
                or str(binding.get("address_class") or "")
                not in {"dns_name", "private", "loopback", "link_local", "public_ip"}
                or not isinstance(binding.get("effective_port"), int)
                or not digest.startswith("sha256:")
                or len(digest) != 71
            ):
                blockers.append(f"{name}:destination_binding_invalid")
        if posture == "stable" and binding:
            if str(binding.get("scheme") or "") != "https":
                blockers.append(f"{name}:https_required")
            address_class = str(binding.get("address_class") or "")
            egress = bool(config.get("operator_egress_enforced"))
            if address_class == "dns_name" and not (
                egress and str(config.get("dns_rebinding_protection") or "") == "operator_egress"
            ):
                blockers.append(f"{name}:dns_rebinding_control_missing")
            if address_class in {"private", "loopback", "link_local"} and not (
                egress and str(config.get("network_scope") or "") == "policy_bound"
            ):
                blockers.append(f"{name}:private_scope_not_policy_bound")
        checked.append(
            {
                "connector": str(name),
                "posture": posture,
                "scheme": str(binding.get("scheme") or ""),
                "address_class": str(binding.get("address_class") or ""),
                "origin_binding_digest": str(binding.get("origin_binding_digest") or ""),
            }
        )
    if blockers:
        return _check(
            "network_egress_posture",
            CHECK_BLOCKER,
            "HTTP connector network posture is not fail-closed",
            details={"blockers": sorted(blockers), "connectors": checked},
            next_action=(
                "declare stable HTTPS destination binding and operator DNS/egress controls, "
                "or use explicit lab/fixture posture"
            ),
        )
    return _check(
        "network_egress_posture",
        CHECK_PASSED,
        "HTTP connector network posture is bounded",
        details={"connectors": checked},
    )


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
