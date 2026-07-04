from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from rexecop.errors import RExecOpValidationError
from rexecop.profile.conformance import PROFILE_CONFORMANCE_SCHEMA, validate_profile_conformance

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PROFILE = ROOT / "examples/profiles/runtime-fixture"


def _profile(tmp_path: Path) -> Path:
    root = tmp_path / "runtime-fixture-conformance"
    shutil.copytree(SOURCE_PROFILE, root)
    for relative in (
        "intents/apply_fixture_change.yaml",
        "workflows/apply_fixture_change.yaml",
        "validation_rules/apply_fixture_change.yaml",
    ):
        (root / relative).unlink()
    intent_path = root / "intents" / "inspect_fixture_state.yaml"
    intent = yaml.safe_load(intent_path.read_text(encoding="utf-8"))
    intent["intent"]["enforce_declared_modes"] = True
    intent["intent"]["facts_contract"] = "fixture.state@1.0"
    intent["intent"]["catalog"] = {
        "title": "Inspect fixture state",
        "summary": "Read a bounded fixture state for conformance tests.",
        "target_kinds": ["fixture"],
        "required_capabilities": ["fixture.readonly"],
        "side_effect_class": "none",
        "validation_ref": "validation_rules/inspect_fixture_state.yaml",
        "runbook_ref": "docs/fixture.md",
    }
    intent["intent"]["reaction_observation"] = {
        "shared_state_key": "reaction_observation",
        "schema_ref": "schemas/observation_envelope.v0.1.schema.json",
        "source_intent": "inspect_fixture_state",
        "producer_step": "record_execution_checkpoint",
        "requires_completed_operation": True,
    }
    intent_path.write_text(yaml.safe_dump(intent, sort_keys=False), encoding="utf-8")
    workflow_path = root / "workflows" / "inspect_fixture_state.yaml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    workflow["workflow"]["steps"].append(
        {
            "id": "record_execution_checkpoint",
            "type": "internal",
            "action": "record_execution_checkpoint",
            "pause_safe": True,
        }
    )
    workflow_path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    reactions = root / "reactions"
    reactions.mkdir()
    (reactions / "reaction_pack.yaml").write_text(
        yaml.safe_dump(
            {
                "reaction_pack": {
                    "id": "fixture-conformance-reactions",
                    "version": "0.1",
                    "budgets": {"max_depth": 3, "max_reactions": 5},
                    "rules": [
                        {
                            "id": "fixture.degraded.inspect",
                            "priority": 10,
                            "when": [
                                {
                                    "path": "facts.state.status",
                                    "operator": "equals",
                                    "value": "degraded",
                                }
                            ],
                            "finding": {
                                "kind": "fixture.degraded",
                                "severity": "medium",
                                "summary": "Fixture state is degraded.",
                            },
                            "outcome": "run_intent",
                            "intent_ref": "inspect_fixture_state",
                        }
                    ],
                    "fallback": {
                        "id": "fixture.fallback",
                        "priority": 1000,
                        "when": [],
                        "finding": {
                            "kind": "fixture.no_match",
                            "severity": "info",
                            "summary": "No deterministic reaction matched.",
                        },
                        "outcome": "no_op",
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return root


def test_profile_conformance_accepts_neutral_reaction_observation_contract(
    tmp_path: Path,
) -> None:
    result = validate_profile_conformance(
        _profile(tmp_path),
        require_reaction_observation=True,
        require_readonly=True,
    )

    assert result.status == "passed"
    assert result.reaction_observation_intents == ("inspect_fixture_state",)
    assert "reaction_pack" in result.checked_surfaces
    assert result.errors == ()


def test_profile_conformance_rejects_malformed_reaction_observation(
    tmp_path: Path,
) -> None:
    root = _profile(tmp_path)
    intent_path = root / "intents" / "inspect_fixture_state.yaml"
    intent = yaml.safe_load(intent_path.read_text(encoding="utf-8"))
    intent["intent"]["reaction_observation"]["schema_ref"] = "schemas/other.json"
    intent["intent"]["reaction_observation"]["producer_step"] = "missing"
    intent_path.write_text(yaml.safe_dump(intent, sort_keys=False), encoding="utf-8")

    result = validate_profile_conformance(
        root,
        require_reaction_observation=True,
        require_readonly=True,
    )

    assert result.status == "failed"
    assert "inspect_fixture_state:reaction_observation:schema_ref" in result.errors
    assert "inspect_fixture_state:reaction_observation:producer_step_not_found" in (
        result.errors
    )


def test_profile_conformance_requires_reaction_observation_when_requested(
    tmp_path: Path,
) -> None:
    root = _profile(tmp_path)
    intent_path = root / "intents" / "inspect_fixture_state.yaml"
    intent = yaml.safe_load(intent_path.read_text(encoding="utf-8"))
    intent["intent"].pop("reaction_observation")
    intent_path.write_text(yaml.safe_dump(intent, sort_keys=False), encoding="utf-8")

    result = validate_profile_conformance(root, require_reaction_observation=True)

    assert result.status == "failed"
    assert "reaction_observation:not_declared" in result.errors


def test_profile_conformance_accepts_tecrax_reaction_observation_contract() -> None:
    result = validate_profile_conformance(
        "tecrax",
        require_reaction_observation=True,
        track="readonly",
    )
    assert result.as_dict()["schema"] == PROFILE_CONFORMANCE_SCHEMA

    assert result.status == "passed"
    assert result.track == "readonly"
    assert "diagnose_monitoring_host" in result.reaction_observation_intents
    assert "configure_chrony_ntp_server" in result.mutation_candidate_intents
    assert "configure_chrony_ntp_server" in result.skipped_intents
    assert "reaction_pack" in result.checked_surfaces


def test_profile_conformance_reports_tecrax_mutation_track_separately() -> None:
    result = validate_profile_conformance("tecrax", track="mutation")

    assert result.status == "passed"
    assert result.track == "mutation"
    assert result.checked_intents == ("configure_chrony_ntp_server",)
    assert result.mutation_candidate_intents == ("configure_chrony_ntp_server",)
    assert "diagnose_monitoring_host" in result.skipped_intents
    assert result.reaction_observation_intents == ()


def test_profile_conformance_rejects_unknown_track(tmp_path: Path) -> None:
    with pytest.raises(RExecOpValidationError, match="profile conformance track"):
        validate_profile_conformance(_profile(tmp_path), track="invalid")


def test_profile_conformance_raises_for_unknown_profile() -> None:
    with pytest.raises(RExecOpValidationError):
        validate_profile_conformance("not-a-real-profile")
