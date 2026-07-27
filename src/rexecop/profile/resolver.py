from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path

from rexecop.errors import RExecOpValidationError
from rexecop.plugins.diagnostic_identity import (
    ProjectedDiagnosticIdentity,
    project_diagnostic_identity,
)

PROFILE_ENTRY_GROUP = "rexecop.profiles"


@dataclass(frozen=True, slots=True)
class _ProfileResolutionFailure:
    identity: ProjectedDiagnosticIdentity
    reason_code: str
    exception_class: ProjectedDiagnosticIdentity


@dataclass(frozen=True, slots=True)
class _ProfileResolutionSnapshot:
    path: Path | None
    failure: _ProfileResolutionFailure | None


def _iter_profile_entry_points() -> list:
    return list(entry_points(group=PROFILE_ENTRY_GROUP))


def resolve_profile_path(profile: str | Path) -> Path:
    """Resolve a filesystem path or registered profile name to profile.yaml or root dir."""
    if isinstance(profile, Path):
        candidate = profile.expanduser()
        if candidate.exists():
            return candidate
        raise RExecOpValidationError(f"profile not found: {profile}")

    text = profile.strip()
    if not text:
        raise RExecOpValidationError("profile path or name is required")

    # Bare registered names win over accidental cwd directories (e.g. CI checkouts).
    if "/" not in text and "\\" not in text and not text.startswith("."):
        registered = _profile_entry_path(text)
        if registered is not None:
            return registered

    candidate = Path(text).expanduser()
    if candidate.exists():
        return candidate

    registered = _profile_entry_path(text)
    if registered is not None:
        return registered

    raise RExecOpValidationError(f"profile not found: {profile}")


def list_registered_profiles() -> list[str]:
    return sorted(ep.name for ep in _iter_profile_entry_points())


def _profile_entry_path(name: str) -> Path | None:
    snapshot = _snapshot_profile_resolution(name)
    if snapshot.path is not None:
        return snapshot.path
    if snapshot.failure is not None:
        failure = snapshot.failure
        raise RExecOpValidationError(
            f"profile entry {failure.identity.display!r} failed: "
            f"{failure.reason_code}:{failure.exception_class.display}"
        ) from None
    return None


def _snapshot_profile_resolution(name: str) -> _ProfileResolutionSnapshot:
    identity = project_diagnostic_identity(name, kind="profile")
    try:
        points = _iter_profile_entry_points()
    except Exception as exc:  # noqa: BLE001 - complete diagnostic boundary
        return _failed_profile_snapshot(identity, "entry_point_enumeration_failed", exc)

    last_failure: _ProfileResolutionFailure | None = None
    for ep in points:
        try:
            raw_entry_name = ep.name
        except Exception as exc:  # noqa: BLE001 - untrusted entry-point metadata
            last_failure = _profile_failure(identity, "entry_point_name_failed", exc)
            continue
        try:
            matches_name = bool(raw_entry_name == name)
        except Exception as exc:  # noqa: BLE001 - untrusted entry-point metadata
            last_failure = _profile_failure(identity, "entry_point_name_failed", exc)
            continue
        if not matches_name:
            continue
        try:
            registrar = ep.load()
        except Exception as exc:  # noqa: BLE001 - try next duplicate entry point
            last_failure = _profile_failure(identity, "entry_point_load_failed", exc)
            continue
        try:
            loaded = registrar()
        except Exception as exc:  # noqa: BLE001 - try next duplicate entry point
            last_failure = _profile_failure(identity, "entry_point_invocation_failed", exc)
            continue
        try:
            raw_path = str(loaded)
        except Exception as exc:  # noqa: BLE001 - untrusted profile result
            last_failure = _profile_failure(identity, "profile_path_conversion_failed", exc)
            continue
        try:
            root = Path(raw_path).expanduser().resolve()
        except Exception as exc:  # noqa: BLE001 - untrusted path expansion/resolution
            last_failure = _profile_failure(identity, "profile_path_resolution_failed", exc)
            continue
        try:
            is_directory = root.is_dir()
        except Exception as exc:  # noqa: BLE001 - filesystem validation boundary
            last_failure = _profile_failure(identity, "profile_path_validation_failed", exc)
            continue
        if not is_directory:
            last_failure = _ProfileResolutionFailure(
                identity=identity,
                reason_code="profile_path_not_directory",
                exception_class=project_diagnostic_identity(
                    "NotADirectoryError", kind="exception"
                ),
            )
            continue
        try:
            profile_file = root / "profile.yaml"
            has_profile_file = profile_file.is_file()
        except Exception as exc:  # noqa: BLE001 - filesystem validation boundary
            last_failure = _profile_failure(identity, "profile_path_validation_failed", exc)
            continue
        if has_profile_file:
            return _ProfileResolutionSnapshot(path=profile_file, failure=None)
        return _ProfileResolutionSnapshot(path=root, failure=None)
    return _ProfileResolutionSnapshot(path=None, failure=last_failure)


def _failed_profile_snapshot(
    identity: ProjectedDiagnosticIdentity,
    reason_code: str,
    exc: Exception,
) -> _ProfileResolutionSnapshot:
    return _ProfileResolutionSnapshot(
        path=None,
        failure=_profile_failure(identity, reason_code, exc),
    )


def _profile_failure(
    identity: ProjectedDiagnosticIdentity,
    reason_code: str,
    exc: Exception,
) -> _ProfileResolutionFailure:
    try:
        raw_class_name = type(exc).__name__
    except Exception:  # noqa: BLE001 - untrusted exception class metadata
        raw_class_name = "Exception"
    return _ProfileResolutionFailure(
        identity=identity,
        reason_code=reason_code,
        exception_class=project_diagnostic_identity(raw_class_name, kind="exception"),
    )
