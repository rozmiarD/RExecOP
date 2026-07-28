from __future__ import annotations

import os
import shutil
import tempfile
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final

EXAMPLE_NAME: Final = "first-run-demo"
FIXTURE_VERSION: Final = "v0.1.0"
FIXTURE_RESOURCE_DIRECTORY: Final = "v0_1_0"
FIXTURE_FILES: Final = (
    "catalog.yaml",
    "environment.yaml",
    "profile/connectors/fixture.yaml",
    "profile/docs/inspect.md",
    "profile/intents/inspect.yaml",
    "profile/profile.yaml",
    "profile/validation_rules/inspect.yaml",
    "profile/workflows/inspect.yaml",
)
NONCLAIMS: Final = (
    "does not initialize a runtime root, run doctor or plan, register a profile, "
    "execute a connector, issue admission or emit canonical evidence",
    "is not a sandbox, scheduler, policy engine, security review, "
    "production-readiness claim or domain profile",
    "does not overwrite, merge, force, adopt, migrate, upgrade or downgrade "
    "an existing materialization",
    "does not guarantee distributed or network-filesystem atomicity, power-loss "
    "durability or hostile shared-directory race safety",
)


class FirstRunMaterializationError(ValueError):
    """A bounded failure while copying the static first-run fixture."""


def materialize_first_run_demo(output: Path) -> dict[str, object]:
    """Copy the closed packaged fixture once into a new local directory."""
    resources = _validated_resources()
    destination = output.absolute()
    _validate_destination(destination)
    staging = Path(tempfile.mkdtemp(prefix=".rexecop-first-run-", dir=destination.parent))
    try:
        for relative, content in resources:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if any((staging / relative).read_bytes() != content for relative, content in resources):
            raise FirstRunMaterializationError("packaged first-run fixture is invalid")
        if destination.exists() or destination.is_symlink():
            raise FirstRunMaterializationError("output must name a new directory")
        os.rename(staging, destination)
    except FirstRunMaterializationError:
        raise
    except OSError as exc:
        raise FirstRunMaterializationError("could not materialize first-run fixture") from exc
    finally:
        if staging.exists():
            try:
                shutil.rmtree(staging)
            except OSError:
                pass
    return {
        "status": "materialized",
        "example": EXAMPLE_NAME,
        "fixture_version": FIXTURE_VERSION,
        "output": str(destination),
        "files": list(FIXTURE_FILES),
        "nonclaims": list(NONCLAIMS),
    }


def _validated_resources() -> tuple[tuple[str, bytes], ...]:
    try:
        root = files("rexecop.examples").joinpath(
            "first_run_demo", FIXTURE_RESOURCE_DIRECTORY
        )
        actual = _resource_file_set(root)
        expected = set(FIXTURE_FILES)
        if actual != expected:
            raise FirstRunMaterializationError("packaged first-run fixture is invalid")
        contents: list[tuple[str, bytes]] = []
        for relative in FIXTURE_FILES:
            resource = root.joinpath(*relative.split("/"))
            if not resource.is_file():
                raise FirstRunMaterializationError("packaged first-run fixture is invalid")
            contents.append((relative, resource.read_bytes()))
        return tuple(contents)
    except OSError as exc:
        raise FirstRunMaterializationError("packaged first-run fixture is invalid") from exc


def _resource_file_set(root: Traversable, prefix: str = "") -> set[str]:
    entries = root.iterdir()
    result: set[str] = set()
    for entry in entries:
        name = entry.name
        relative = f"{prefix}/{name}" if prefix else name
        if entry.is_file():
            result.add(relative)
        elif entry.is_dir():
            result.update(_resource_file_set(entry, relative))
        else:
            raise FirstRunMaterializationError("packaged first-run fixture is invalid")
    return result


def _validate_destination(destination: Path) -> None:
    if destination.is_symlink():
        raise FirstRunMaterializationError("output path must not be a symlink")
    if destination.exists():
        raise FirstRunMaterializationError("output must name a new directory")
    for ancestor in (destination.parent, *destination.parent.parents):
        if ancestor.is_symlink():
            raise FirstRunMaterializationError("output ancestor must not be a symlink")
    if not destination.parent.is_dir():
        raise FirstRunMaterializationError("output parent must be an existing directory")
