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
from rexecop.runtime.root_compatibility import runtime_root_compatibility

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
