from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path

from rexecop.errors import RExecOpValidationError

PROFILE_ENTRY_GROUP = "rexecop.profiles"


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
    for ep in _iter_profile_entry_points():
        if ep.name != name:
            continue
        loaded = ep.load()()
        root = Path(str(loaded)).expanduser().resolve()
        if not root.is_dir():
            raise RExecOpValidationError(f"profile entry {name!r} is not a directory: {root}")
        profile_file = root / "profile.yaml"
        if profile_file.is_file():
            return profile_file
        return root
    return None
