from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rexecop.action.configure import _load_environment_document
from rexecop.action.diff import _validate_environment_document
from rexecop.catalog.digest import yaml_document_digest
from rexecop.cli import app
from rexecop.connectors.capability import load_profile_connector_capabilities
from rexecop.environment.loader import load_environment
from rexecop.errors import RExecOpValidationError
from rexecop.profile.loader import LoadedProfile, load_profile
from rexecop.profile.operator_metadata import load_operator_metadata
from rexecop.profile.validation_rules import load_validation_rule_spec
from rexecop.reaction.compiler import compile_reaction_pack
from rexecop.runtime.init import initialize_runtime_root
from rexecop.secrets.resolver import FileSecretResolver
from rexecop.triggers.service import (
    _canonical_json as trigger_canonical_json,
)
from rexecop.triggers.service import (
    _load_trigger_rules,
)
from rexecop.triggers.service import (
    _write_json as write_trigger_json,
)
from rexecop.workflow.loader import load_workflow

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "examples/profiles/runtime-fixture/profile.yaml"
runner = CliRunner()


def _duplicate_document(root_key: str) -> str:
    return f"{root_key}:\n  value: first\n  value: private-second-marker\n"


@pytest.mark.parametrize(
    "loader",
    [
        load_environment,
        load_workflow,
        yaml_document_digest,
        _load_environment_document,
        _validate_environment_document,
    ],
)
def test_direct_file_boundaries_share_structural_rejection(
    tmp_path: Path,
    loader: object,
) -> None:
    path = tmp_path / "private-input.yaml"
    path.write_text(_duplicate_document("environment"), encoding="utf-8")

    with pytest.raises(RExecOpValidationError) as raised:
        loader(path)  # type: ignore[operator]

    assert raised.value.reason_code == "invalid_yaml_structure"
    assert "private-second-marker" not in str(raised.value)
    assert str(path) not in str(raised.value)


def test_profile_and_nested_profile_boundaries_share_parser(tmp_path: Path) -> None:
    profile_root = tmp_path / "profile"
    for relative in ("intents", "workflows", "connectors", "validation_rules"):
        (profile_root / relative).mkdir(parents=True, exist_ok=True)
    (profile_root / "profile.yaml").write_text(
        _duplicate_document("profile_contract"), encoding="utf-8"
    )

    with pytest.raises(RExecOpValidationError) as profile_error:
        load_profile(profile_root)
    assert profile_error.value.reason_code == "invalid_yaml_structure"

    loaded = load_profile(PROFILE)
    (profile_root / "operator_metadata.yaml").write_text(
        _duplicate_document("operator_metadata"), encoding="utf-8"
    )
    loaded.root = profile_root
    with pytest.raises(RExecOpValidationError) as operator_error:
        load_operator_metadata(loaded)
    assert operator_error.value.reason_code == "invalid_yaml_structure"

    (profile_root / "connectors" / "fixture.yaml").write_text(
        _duplicate_document("connector"), encoding="utf-8"
    )
    with pytest.raises(RExecOpValidationError) as connector_error:
        load_profile_connector_capabilities(profile_root, "fixture")
    assert connector_error.value.reason_code == "invalid_yaml_structure"

    (profile_root / "validation_rules" / "inspect.yaml").write_text(
        _duplicate_document("validation_rule"), encoding="utf-8"
    )
    with pytest.raises(RExecOpValidationError) as validation_error:
        load_validation_rule_spec(profile_root, "inspect")
    assert validation_error.value.reason_code == "invalid_yaml_structure"


def test_intent_and_connector_contract_methods_share_parser(tmp_path: Path) -> None:
    (tmp_path / "intents").mkdir()
    (tmp_path / "connectors").mkdir()
    (tmp_path / "intents" / "inspect.yaml").write_text(
        _duplicate_document("intent"), encoding="utf-8"
    )
    (tmp_path / "connectors" / "fixture.yaml").write_text(
        _duplicate_document("connector"), encoding="utf-8"
    )
    profile = LoadedProfile(root=tmp_path, contract={}, name="fixture", version="1")

    with pytest.raises(RExecOpValidationError) as intent_error:
        profile.intent_metadata("inspect")
    with pytest.raises(RExecOpValidationError) as connector_error:
        profile.connector_contract("fixture")

    assert intent_error.value.reason_code == "invalid_yaml_structure"
    assert connector_error.value.reason_code == "invalid_yaml_structure"


def test_reaction_and_trigger_boundaries_keep_reaction_byte_limit(tmp_path: Path) -> None:
    loaded = load_profile(PROFILE)
    reaction = tmp_path / "reaction.yaml"
    reaction.write_text(_duplicate_document("reaction_pack"), encoding="utf-8")
    with pytest.raises(RExecOpValidationError) as reaction_error:
        compile_reaction_pack(loaded, reaction)
    assert reaction_error.value.reason_code == "invalid_yaml_structure"

    profile_root = tmp_path / "profile"
    (profile_root / "triggers").mkdir(parents=True)
    (profile_root / "triggers" / "trigger_rules.yaml").write_text(
        _duplicate_document("trigger_rules"), encoding="utf-8"
    )
    loaded.root = profile_root
    with pytest.raises(RExecOpValidationError) as trigger_error:
        _load_trigger_rules(loaded)
    assert trigger_error.value.reason_code == "invalid_yaml_structure"


def test_trigger_json_writers_reject_non_finite_before_artifact(tmp_path: Path) -> None:
    path = tmp_path / "trigger-state.json"
    value = {"nested": {"invalid": float("nan")}}

    with pytest.raises(RExecOpValidationError) as canonical_error:
        trigger_canonical_json(value)
    with pytest.raises(RExecOpValidationError) as writer_error:
        write_trigger_json(path, value)

    assert canonical_error.value.reason_code == "invalid_json_value"
    assert writer_error.value.reason_code == "invalid_json_value"
    assert not path.exists()


def test_secret_file_boundary_rejects_duplicate_before_value_lookup(tmp_path: Path) -> None:
    path = tmp_path / "secrets.yaml"
    path.write_text(
        "secrets:\n  fixture_ref: first-private\n  fixture_ref: second-private\n",
        encoding="utf-8",
    )
    path.chmod(0o600)

    with pytest.raises(RExecOpValidationError) as raised:
        FileSecretResolver(path).resolve("fixture_ref")

    assert raised.value.reason_code == "invalid_yaml_structure"
    assert "private" not in str(raised.value)


def test_env_lint_and_runtime_plan_return_same_redacted_reason_before_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = tmp_path / "private-environment.yaml"
    environment.write_text(
        "environment:\n  id: first\n  id: secret-id-marker\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.chdir(runtime)
    initialize_runtime_root(runtime / ".rexecop")

    lint = runner.invoke(app, ["--json", "env", "lint", "--env", str(environment)])
    plan = runner.invoke(
        app,
        [
            "--json",
            "plan",
            "--profile",
            str(PROFILE),
            "--env",
            str(environment),
            "--intent",
            "inspect_fixture_state",
            "--target",
            "fixture-target",
        ],
    )

    assert lint.exit_code == plan.exit_code == 1
    for result in (lint, plan):
        payload = json.loads(result.stdout)
        assert payload["reason_code"] == "invalid_yaml_structure"
        assert payload["message"] == "YAML input is invalid or exceeds structural limits"
        assert "secret-id-marker" not in result.stdout
        assert str(environment) not in result.stdout
    assert not list((runtime / ".rexecop" / "operations").glob("*.json"))
    assert not list((runtime / ".rexecop" / "plans").glob("*.json"))
