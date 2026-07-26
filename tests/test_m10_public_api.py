from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.main import get_command

from rexecop import cli as cli_module
from rexecop.cli_contracts import CLI_CONTRACTS, cli_contract_registry
from rexecop.errors import RExecOpValidationError
from rexecop.public_api import (
    ALPHA_CLI_COMMANDS,
    PUBLIC_API_SCHEMA,
    SUPPORTED_PUBLIC_IMPORTS,
    public_api_manifest,
)
from rexecop.runtime import init as runtime_init
from rexecop.runtime.contract_compatibility import validate_rexecop_projection_version
from rexecop.runtime.root_compatibility import (
    RUNTIME_MANIFEST_MAX_BYTES,
    runtime_root_compatibility,
)
from rexecop.storage.factory import create_store

ROOT = Path(__file__).resolve().parents[1]


def _cli_leaf_commands(command: object, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    commands = getattr(command, "commands", None)
    if not isinstance(commands, dict):
        return set()
    leaves: set[tuple[str, ...]] = set()
    for name, child in commands.items():
        path = (*prefix, str(name))
        child_commands = getattr(child, "commands", None)
        if isinstance(child_commands, dict):
            leaves.update(_cli_leaf_commands(child, path))
        else:
            leaves.add(path)
    return leaves


def test_supported_public_imports_load_in_fresh_subprocess() -> None:
    imports = [item.as_dict() for item in SUPPORTED_PUBLIC_IMPORTS]
    code = (
        "import importlib, json\n"
        f"imports = json.loads({json.dumps(json.dumps(imports))})\n"
        "for item in imports:\n"
        "    module = importlib.import_module(item['module'])\n"
        "    for symbol in item['symbols']:\n"
        "        assert hasattr(module, symbol), f\"{item['module']}:{symbol}\"\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_public_api_manifest_is_explicit_and_deterministic() -> None:
    manifest = public_api_manifest()

    assert manifest["schema"] == PUBLIC_API_SCHEMA
    assert manifest["python_api"]["stability"] == "stable_v1"
    assert manifest["schema_compatibility_policy"] == "unknown_major_fail_closed"
    assert manifest["runtime_root_upgrade_policy"] == "alpha_root_requires_new_v1_root"
    assert (
        "This manifest does not certify production or mutation readiness."
        in manifest["non_claims"]
    )
    assert manifest == public_api_manifest()


def test_every_cli_leaf_is_stable_registered_or_explicitly_alpha() -> None:
    actual = _cli_leaf_commands(get_command(cli_module.app))
    stable = {item.command for item in CLI_CONTRACTS}
    alpha = set(ALPHA_CLI_COMMANDS)

    assert not stable.intersection(alpha)
    assert actual == stable.union(alpha)
    assert {item.stability for item in CLI_CONTRACTS} == {"stable_v1"}
    assert {
        tuple(item["argv"])
        for item in cli_contract_registry()["contracts"]
    } == stable


def test_schema_compatibility_fails_closed_on_unknown_major() -> None:
    with pytest.raises(
        RExecOpValidationError,
        match="unsupported_runtime_projection_major_version",
    ):
        validate_rexecop_projection_version("runtime_manifest", "v9.0")


def test_alpha_runtime_root_requires_new_root_for_v1() -> None:
    decision = runtime_root_compatibility(
        {
            "schema": "rexecop.runtime_init.v0.1",
            "rexecop_version": "0.3.0rc3",
        },
        target_version="1.0.0",
    )

    assert decision["status"] == "new_root_required"
    assert decision["reason_code"] == "runtime_root_new_root_required"
    assert decision["in_place_upgrade_supported"] is False
    assert decision["new_root_required"] is True


@pytest.mark.parametrize(
    ("manifest", "target_version", "configured_backend", "manifest_present", "reason_code"),
    [
        (None, "1.0.0", "file", False, "runtime_root_manifest_missing"),
        ([], "1.0.0", "file", True, "runtime_root_manifest_invalid"),
        (
            {
                "schema": "rexecop.runtime_init.v9.0",
                "rexecop_version": "1.0.0",
                "storage_backend": "file",
            },
            "1.0.0",
            "file",
            True,
            "runtime_root_manifest_schema_unsupported",
        ),
        (
            {
                "schema": "rexecop.runtime_init.v0.1",
                "rexecop_version": "0.3.0rc3",
                "storage_backend": "file",
            },
            "1.0.0",
            "file",
            True,
            "runtime_root_new_root_required",
        ),
        (
            {
                "schema": "rexecop.runtime_init.v0.1",
                "rexecop_version": "1.9.0",
                "storage_backend": "file",
            },
            "2.0.0",
            "file",
            True,
            "runtime_root_major_version_unsupported",
        ),
        (
            {
                "schema": "rexecop.runtime_init.v0.1",
                "rexecop_version": "2.0.0",
                "storage_backend": "file",
            },
            "1.0.0",
            "file",
            True,
            "runtime_root_downgrade_unsupported",
        ),
        (
            {
                "schema": "rexecop.runtime_init.v0.1",
                "rexecop_version": "1.0.0",
                "storage_backend": "file",
            },
            "1.0.0",
            "sqlite",
            True,
            "runtime_root_storage_backend_mismatch",
        ),
    ],
)
def test_runtime_root_compatibility_uses_one_fail_closed_decision(
    manifest: object,
    target_version: str,
    configured_backend: str,
    manifest_present: bool,
    reason_code: str,
) -> None:
    decision = runtime_root_compatibility(
        manifest,
        target_version=target_version,
        configured_storage_backend=configured_backend,
        manifest_present=manifest_present,
    )

    assert decision["status"] != "compatible"
    assert decision["reason_code"] == reason_code
    assert decision["in_place_upgrade_supported"] is False
    assert decision["guidance"]


def test_init_refuses_to_overwrite_alpha_root_on_v1(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "alpha-root"
    root.mkdir()
    (root / "runtime_manifest.json").write_text(
        json.dumps(
            {
                "schema": "rexecop.runtime_init.v0.1",
                "rexecop_version": "0.3.0rc3",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_init, "__version__", "1.0.0")

    with pytest.raises(RExecOpValidationError, match="runtime_root_new_root_required"):
        runtime_init.initialize_runtime_root(root)


def test_store_factory_rejects_missing_manifest_before_sqlite_mutation(tmp_path: Path) -> None:
    root = tmp_path / "missing-root"

    with pytest.raises(RExecOpValidationError, match="runtime_root_manifest_missing"):
        create_store(root, backend="sqlite")

    assert not root.exists()


@pytest.mark.parametrize("precreate_empty_root", [False, True])
def test_init_accepts_only_absent_or_empty_real_root(
    tmp_path: Path,
    precreate_empty_root: bool,
) -> None:
    root = tmp_path / "new-root"
    if precreate_empty_root:
        root.mkdir()

    result = runtime_init.initialize_runtime_root(root, backend="file")

    assert result["status"] == "initialized"
    assert (root / "runtime_manifest.json").is_file()


def test_init_rejects_nonempty_manifestless_root_without_side_effects(
    tmp_path: Path,
) -> None:
    root = tmp_path / "nonempty-root"
    root.mkdir()
    sentinel = root / "operator-data.bin"
    sentinel_bytes = b"preserve-existing-runtime-bytes\n"
    sentinel.write_bytes(sentinel_bytes)

    with pytest.raises(
        RExecOpValidationError,
        match="runtime_root_manifest_missing_nonempty",
    ):
        runtime_init.initialize_runtime_root(root, backend="sqlite")

    assert sentinel.read_bytes() == sentinel_bytes
    assert sorted(path.name for path in root.iterdir()) == [sentinel.name]
    assert not (root / "runtime_manifest.json").exists()
    assert not (root / "queue").exists()
    assert not (root / "rexecop.db").exists()


@pytest.mark.parametrize("symlink_kind", ["selected-root", "ancestor"])
def test_init_rejects_symlink_root_path_without_adoption(
    tmp_path: Path,
    symlink_kind: str,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    sentinel = real_parent / "operator-data.bin"
    sentinel_bytes = b"preserve-symlink-target-bytes\n"
    sentinel.write_bytes(sentinel_bytes)
    linked = tmp_path / "linked"
    if symlink_kind == "selected-root":
        linked.symlink_to(real_parent, target_is_directory=True)
        selected_root = linked
    else:
        linked.symlink_to(real_parent, target_is_directory=True)
        selected_root = linked / "new-root"

    with pytest.raises(RExecOpValidationError, match="runtime_root_path_invalid"):
        runtime_init.initialize_runtime_root(selected_root, backend="sqlite")

    assert sentinel.read_bytes() == sentinel_bytes
    assert not (real_parent / "runtime_manifest.json").exists()
    assert not (real_parent / "queue").exists()
    assert not (real_parent / "rexecop.db").exists()
    assert linked.is_symlink()


@pytest.mark.parametrize(
    "manifest_bytes",
    [
        (
            b'{"schema":"rexecop.runtime_init.v0.1",'
            b'"schema":"rexecop.runtime_init.v0.1",'
            b'"rexecop_version":"1.0.0rc1","storage_backend":"file"}\n'
        ),
        (
            b'{"schema":"rexecop.runtime_init.v0.1",'
            b'"rexecop_version":"1.0.0rc1","storage_backend":"file",'
            b'"metadata":{"note":"one","note":"two"}}\n'
        ),
    ],
)
def test_init_rejects_duplicate_manifest_keys_without_side_effects(
    tmp_path: Path,
    manifest_bytes: bytes,
) -> None:
    root = tmp_path / "duplicate-manifest-root"
    root.mkdir()
    manifest_path = root / "runtime_manifest.json"
    manifest_path.write_bytes(manifest_bytes)

    with pytest.raises(RExecOpValidationError, match="runtime_root_manifest_invalid"):
        runtime_init.initialize_runtime_root(root, backend="sqlite")

    assert manifest_path.read_bytes() == manifest_bytes
    assert sorted(path.name for path in root.iterdir()) == [manifest_path.name]
    assert not (root / "queue").exists()
    assert not (root / "rexecop.db").exists()


def test_init_rejects_oversized_valid_manifest_without_side_effects(
    tmp_path: Path,
) -> None:
    root = tmp_path / "oversized-manifest-root"
    root.mkdir()
    manifest_path = root / "runtime_manifest.json"
    manifest_bytes = json.dumps(
        {
            "schema": "rexecop.runtime_init.v0.1",
            "rexecop_version": "1.0.0rc1",
            "storage_backend": "file",
            "padding": "x" * RUNTIME_MANIFEST_MAX_BYTES,
        }
    ).encode("utf-8")
    assert len(manifest_bytes) > RUNTIME_MANIFEST_MAX_BYTES
    manifest_path.write_bytes(manifest_bytes)

    with pytest.raises(RExecOpValidationError, match="runtime_root_manifest_invalid"):
        runtime_init.initialize_runtime_root(root, backend="sqlite")

    assert manifest_path.read_bytes() == manifest_bytes
    assert sorted(path.name for path in root.iterdir()) == [manifest_path.name]
    assert not (root / "queue").exists()
    assert not (root / "rexecop.db").exists()


def test_init_rejects_backend_mismatch_without_rewriting_current_root(tmp_path: Path) -> None:
    root = tmp_path / "file-root"
    runtime_init.initialize_runtime_root(root, backend="file")
    manifest_path = root / "runtime_manifest.json"
    manifest_before = manifest_path.read_bytes()

    with pytest.raises(
        RExecOpValidationError,
        match="runtime_root_storage_backend_mismatch",
    ):
        runtime_init.initialize_runtime_root(root, backend="sqlite")

    assert manifest_path.read_bytes() == manifest_before
    assert not (root / "rexecop.db").exists()
