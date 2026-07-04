from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sclite.bundles import review_bundle

from rexecop.adapters.sclite_port.full_bundle import (
    CARRIER_PROFILE_REF_FILE,
    TRUST_PROFILE_REF_FILE,
)
from rexecop.environment.loader import load_environment
from rexecop.environment.sanitize import validate_no_inline_secrets
from rexecop.errors import RExecOpValidationError
from rexecop.evidence.redaction import (
    REDACTED,
    SECRET_KEY_PATTERN,
    contains_strong_secret_pattern,
)
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.profile.loader import LoadedProfile, load_profile
from rexecop.profile.resolver import resolve_profile_path
from rexecop.storage.file_store import FileStore

PROFILE_WORKFLOW_HARNESS_SCHEMA = "rexecop.profile_workflow_harness.v0.1"
CHECK_PASSED: HarnessCheckStatus = "passed"
CHECK_FAILED: HarnessCheckStatus = "failed"
CHECK_SKIPPED: HarnessCheckStatus = "skipped"

HarnessCheckStatus = Literal["passed", "failed", "skipped"]

HARNESS_CHECK_IDS = (
    "dry_run_fixture",
    "no_secret_evidence",
    "sclite_bundle_shape",
    "policy_blocked_path",
)


@dataclass(frozen=True)
class HarnessFixture:
    profile_path: Path
    environment_path: Path
    readonly_intent: str
    blocked_intent: str
    target: str
    blocked_mode: str = "apply"


def resolve_harness_fixture(profile: str | Path) -> HarnessFixture | None:
    """Resolve bundled runtime-fixture paths when the profile ships harness examples."""
    loaded = load_profile(resolve_profile_path(profile))
    if loaded.name != "runtime_fixture":
        return None

    examples_root = loaded.root.parent.parent
    environment_path = examples_root / "environments" / "runtime-fixture.policy.example.yaml"
    if not environment_path.is_file():
        return None
    intents_dir = loaded.root / "intents"
    if not (intents_dir / "inspect_fixture_state.yaml").is_file():
        return None
    if not (intents_dir / "apply_fixture_change.yaml").is_file():
        return None

    return HarnessFixture(
        profile_path=loaded.root / "profile.yaml",
        environment_path=environment_path,
        readonly_intent="inspect_fixture_state",
        blocked_intent="apply_fixture_change",
        target="fixture-target",
    )


def run_profile_workflow_harness(
    profile: str | Path,
    *,
    fixture: HarnessFixture | None = None,
    store_root: Path | None = None,
) -> dict[str, Any]:
    """Exercise profile workflows against the domain-neutral runtime-fixture harness."""
    resolved_fixture = fixture or resolve_harness_fixture(profile)
    if resolved_fixture is None:
        return _skipped_harness(profile, reason="no_fixture_environment_configured")

    loaded = load_profile(resolved_fixture.profile_path)
    checks: list[dict[str, Any]] = []
    operation_id = ""
    controller: OperationController | None = None

    if store_root is None:
        raise RExecOpValidationError("workflow harness requires an ephemeral store_root path")

    controller = OperationController(store=FileStore(store_root))
    dry_run = _check_dry_run_fixture(controller, resolved_fixture, loaded)
    checks.append(dry_run)
    operation_id = str((dry_run.get("details") or {}).get("operation_id") or "")

    if dry_run["status"] == CHECK_PASSED and operation_id:
        checks.append(_check_no_secret_evidence(controller, operation_id))
        checks.append(_check_sclite_bundle_shape(controller, operation_id))
    else:
        checks.extend(
            _skipped_dependent_check(check_id)
            for check_id in ("no_secret_evidence", "sclite_bundle_shape")
        )

    checks.append(_check_policy_blocked_path(controller, resolved_fixture, loaded))

    status = CHECK_PASSED
    if any(check["status"] == CHECK_FAILED for check in checks):
        status = CHECK_FAILED
    elif all(check["status"] == CHECK_SKIPPED for check in checks):
        status = CHECK_SKIPPED

    return {
        "schema": PROFILE_WORKFLOW_HARNESS_SCHEMA,
        "status": status,
        "profile": loaded.name,
        "fixture_profile": loaded.name,
        "requires_runtime_store": True,
        "requires_fixture_environment": True,
        "checks": checks,
        "summary": {
            "passed": sum(1 for check in checks if check["status"] == CHECK_PASSED),
            "failed": sum(1 for check in checks if check["status"] == CHECK_FAILED),
            "skipped": sum(1 for check in checks if check["status"] == CHECK_SKIPPED),
        },
    }


def _check_dry_run_fixture(
    controller: OperationController,
    fixture: HarnessFixture,
    loaded: LoadedProfile,
) -> dict[str, Any]:
    validate_no_inline_secrets(load_environment(fixture.environment_path).as_dict())

    try:
        operation = controller.plan(
            profile_path=fixture.profile_path,
            environment_path=fixture.environment_path,
            intent=fixture.readonly_intent,
            target=fixture.target,
            mode="dry_run",
        )
        completed = controller.start(operation.id)
    except RExecOpValidationError as exc:
        return _check(
            "dry_run_fixture",
            CHECK_FAILED,
            "dry-run fixture workflow did not complete",
            errors=[str(exc)],
        )

    if completed.state != OperationState.COMPLETED.value:
        return _check(
            "dry_run_fixture",
            CHECK_FAILED,
            "dry-run fixture workflow finished in a non-completed state",
            details={"operation_id": completed.id, "state": completed.state},
            errors=[f"unexpected_state:{completed.state}"],
        )

    validation = controller.validate(completed.id)
    if not validation.get("passed"):
        return _check(
            "dry_run_fixture",
            CHECK_FAILED,
            "dry-run fixture validation failed",
            details={"operation_id": completed.id, "validation": validation},
            errors=["validation_failed"],
        )

    return _check(
        "dry_run_fixture",
        CHECK_PASSED,
        "dry-run fixture workflow completed without backend IO",
        details={
            "operation_id": completed.id,
            "intent": fixture.readonly_intent,
            "profile": loaded.name,
            "mode": "dry_run",
        },
    )


def _check_no_secret_evidence(controller: OperationController, operation_id: str) -> dict[str, Any]:
    violations: list[str] = []
    for event in controller.store.list_evidence_events(operation_id):
        path = f"{event.get('event_type', 'event')}:{event.get('event_id', 'unknown')}"
        _collect_secret_violations(event.get("sanitized_payload"), path, violations)

    if violations:
        return _check(
            "no_secret_evidence",
            CHECK_FAILED,
            "evidence events contain unredacted secret material",
            details={"operation_id": operation_id, "violation_count": len(violations)},
            errors=violations[:8],
        )

    events = controller.store.list_evidence_events(operation_id)
    return _check(
        "no_secret_evidence",
        CHECK_PASSED,
        "evidence events passed no-secret redaction checks",
        details={"operation_id": operation_id, "event_count": len(events)},
    )


def _check_sclite_bundle_shape(
    controller: OperationController,
    operation_id: str,
) -> dict[str, Any]:
    try:
        receipt = controller.export_receipt(operation_id)
        bundle_dir = Path(str(receipt["bundle_dir"]))
    except (KeyError, TypeError, RExecOpValidationError) as exc:
        return _check(
            "sclite_bundle_shape",
            CHECK_FAILED,
            "execution receipt export failed",
            errors=[str(exc)],
        )

    required_sidecars = (TRUST_PROFILE_REF_FILE, CARRIER_PROFILE_REF_FILE)
    missing = [name for name in required_sidecars if not (bundle_dir / name).is_file()]
    if missing:
        return _check(
            "sclite_bundle_shape",
            CHECK_FAILED,
            "SCLite bundle is missing required sidecar artifacts",
            details={"bundle_dir": str(bundle_dir), "missing": missing},
            errors=[f"missing_sidecar:{name}" for name in missing],
        )

    try:
        record = review_bundle(bundle_dir)
    except Exception as exc:  # noqa: BLE001 - surface bounded harness failure
        return _check(
            "sclite_bundle_shape",
            CHECK_FAILED,
            "SCLite review_bundle rejected harness output",
            details={"bundle_dir": str(bundle_dir)},
            errors=[str(exc)],
        )

    if record.get("verdict") != "pass":
        return _check(
            "sclite_bundle_shape",
            CHECK_FAILED,
            "SCLite bundle review verdict was not pass",
            details={
                "bundle_dir": str(bundle_dir),
                "verdict": record.get("verdict"),
                "checks": record.get("checks"),
            },
            errors=[f"review_verdict:{record.get('verdict', 'unknown')}"],
        )

    return _check(
        "sclite_bundle_shape",
        CHECK_PASSED,
        "SCLite bundle shape and review verdict passed",
        details={
            "bundle_dir": str(bundle_dir),
            "verdict": record.get("verdict"),
            "review_checks": [item.get("name") for item in record.get("checks", [])],
        },
    )


def _check_policy_blocked_path(
    controller: OperationController,
    fixture: HarnessFixture,
    loaded: LoadedProfile,
) -> dict[str, Any]:
    try:
        controller.plan(
            profile_path=fixture.profile_path,
            environment_path=fixture.environment_path,
            intent=fixture.blocked_intent,
            target=fixture.target,
            mode=fixture.blocked_mode,
        )
    except RExecOpValidationError as exc:
        message = str(exc)
        if "fixture_mutation_denied" in message or "operation policy denied" in message:
            return _check(
                "policy_blocked_path",
                CHECK_PASSED,
                "policy-blocked mutation path fail-closed before execution",
                details={
                    "intent": fixture.blocked_intent,
                    "mode": fixture.blocked_mode,
                    "profile": loaded.name,
                    "reason": message,
                },
            )
        return _check(
            "policy_blocked_path",
            CHECK_FAILED,
            "mutation path was denied for an unexpected reason",
            errors=[message],
        )

    return _check(
        "policy_blocked_path",
        CHECK_FAILED,
        "policy-blocked mutation path was not rejected at plan time",
        errors=["expected_policy_denial"],
    )


def _collect_secret_violations(value: Any, path: str, violations: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if SECRET_KEY_PATTERN.search(str(key)):
                if item != REDACTED and item not in ("", None):
                    violations.append(f"{child}:secret_key_not_redacted")
            _collect_secret_violations(item, child, violations)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _collect_secret_violations(item, f"{path}[{index}]", violations)
        return
    if isinstance(value, str):
        if contains_strong_secret_pattern(value):
            violations.append(f"{path}:strong_secret_pattern")



def _skipped_dependent_check(check_id: str) -> dict[str, Any]:
    return _check(
        check_id,
        CHECK_SKIPPED,
        "skipped because dry-run fixture did not complete",
        details={"depends_on": "dry_run_fixture"},
    )


def _skipped_harness(profile: str | Path, *, reason: str) -> dict[str, Any]:
    loaded_name = str(profile)
    try:
        loaded_name = load_profile(resolve_profile_path(profile)).name
    except RExecOpValidationError:
        pass
    checks = [
        _check(
            check_id,
            CHECK_SKIPPED,
            "workflow harness fixture is not configured for this profile",
            details={"reason": reason},
        )
        for check_id in HARNESS_CHECK_IDS
    ]
    return {
        "schema": PROFILE_WORKFLOW_HARNESS_SCHEMA,
        "status": CHECK_SKIPPED,
        "profile": loaded_name,
        "requires_runtime_store": False,
        "requires_fixture_environment": True,
        "checks": checks,
        "summary": {"passed": 0, "failed": 0, "skipped": len(checks)},
        "skip_reason": reason,
    }


def _check(
    check_id: str,
    status: HarnessCheckStatus,
    summary: str,
    *,
    details: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": check_id,
        "status": status,
        "summary": summary,
    }
    if details:
        payload["details"] = details
    if errors:
        payload["errors"] = errors
    return payload