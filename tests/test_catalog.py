from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from rexecop.adapters.sclite_port.emitter import (
    build_execution_contract,
    build_intent_contract,
    build_policy_decision,
)
from rexecop.catalog.service import CatalogService
from rexecop.cli import app
from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController


def _profile_contract(name: str) -> dict[str, object]:
    required = {"required": True}
    return {
        "profile_contract": {
            "name": name,
            "version": "1.0",
            "intents": required,
            "workflows": required,
            "connector_requirements": required,
            "risk_classes": required,
            "evidence_requirements": required,
            "governance_expectations": required,
            "validation_rules": required,
            "escalation_rules": required,
        }
    }


def _write_fixture(
    root: Path,
    *,
    profile_name: str = "neutral_alpha",
    capabilities: list[str] | None = None,
    connector_refs: list[str] | None = None,
) -> tuple[Path, Path, Path]:
    profile = root / "profile"
    (profile / "intents").mkdir(parents=True)
    (profile / "workflows").mkdir()
    (profile / "validation_rules").mkdir()
    (profile / "profile.yaml").write_text(
        yaml.safe_dump(_profile_contract(profile_name), sort_keys=False)
    )
    (profile / "intents" / "observe_status.yaml").write_text(
        yaml.safe_dump(
            {
                "intent": {
                    "id": "observe_status",
                    "workflow": "workflows/observe_status.yaml",
                    "risk": "low",
                    "enforce_declared_modes": True,
                    "modes": ["dry_run", "read_only"],
                    "catalog": {
                        "title": "Observe status",
                        "summary": "Read one bounded neutral fixture status.",
                        "target_kinds": ["node"],
                        "required_capabilities": ["fixture_readonly"],
                        "side_effect_class": "none",
                        "validation_ref": "validation_rules/observe_status.yaml",
                        "runbook_ref": "docs/observe-status.md",
                    },
                }
            },
            sort_keys=False,
        )
    )
    (profile / "workflows" / "observe_status.yaml").write_text(
        yaml.safe_dump(
            {
                "workflow": {
                    "id": f"{profile_name}.observe_status",
                    "intent": "observe_status",
                    "mode": "read_only",
                    "risk": "low",
                    "description": "Neutral catalog fixture.",
                    "steps": [
                        {
                            "id": "read_status",
                            "type": "connector",
                            "connector": "fixture_api",
                            "action": "read_status",
                            "pause_safe": True,
                        },
                        {
                            "id": "produce_receipt",
                            "type": "evidence",
                            "action": "produce_receipt",
                            "pause_safe": True,
                        },
                    ],
                }
            },
            sort_keys=False,
        )
    )
    (profile / "validation_rules" / "observe_status.yaml").write_text(
        "validation_rules: []\n"
    )

    environment = root / "environment.yaml"
    environment.write_text(
        yaml.safe_dump(
            {
                "environment": {
                    "id": "private-environment",
                    "profile": profile_name,
                    "description": "Private fixture address must not be rendered.",
                    "targets": {"node-01": {"type": "node", "criticality": "low"}},
                    "connectors": {
                        "fixture_api": {
                            "enabled": True,
                            "backend": "http_api",
                            "base_url": "http://192.0.2.67",
                            "actions": {
                                "read_status": {"method": "GET", "path": "/status"}
                            },
                        }
                    },
                    "safety": {"target_lock_enabled": True},
                }
            },
            sort_keys=False,
        )
    )
    catalog = root / "targets.yaml"
    catalog.write_text(
        yaml.safe_dump(
            {
                "target_catalog": {
                    "version": "0.1",
                    "targets": [
                        {
                            "id": "node-01",
                            "target_kind": "node",
                            "profile_ref": "./profile",
                            "environment_ref": "./environment.yaml",
                            "environment_target": "node-01",
                            "capabilities": capabilities or ["fixture_readonly"],
                            "connector_refs": (
                                ["fixture_api"] if connector_refs is None else connector_refs
                            ),
                            "classification": {"criticality": "low"},
                        }
                    ],
                }
            },
            sort_keys=False,
        )
    )
    return profile, environment, catalog


@pytest.mark.parametrize("profile_name", ["neutral_alpha", "neutral_beta"])
def test_catalog_is_profile_neutral_and_hides_private_paths(
    tmp_path: Path,
    profile_name: str,
) -> None:
    _, _, catalog = _write_fixture(tmp_path, profile_name=profile_name)
    service = CatalogService(catalog)

    targets = service.list_targets()

    assert targets[0]["profile_ref"] == profile_name
    rendered = json.dumps(targets)
    assert "192.0.2.67" not in rendered
    assert str(tmp_path) not in rendered
    assert "environment_ref" not in rendered


def test_operation_projection_requires_admission_after_technical_match(tmp_path: Path) -> None:
    _, _, catalog = _write_fixture(tmp_path)

    result = CatalogService(catalog).list_operations_for_target("node-01")[0]

    assert result["operation"]["id"] == "observe_status"
    assert result["operation"]["required_connectors"] == ["fixture_api"]
    assert result["applicability"]["applicable"] is True
    assert result["applicability"]["status"] == "admission_required"
    assert "allowed" not in json.dumps(result)


def test_operation_projection_reports_missing_capability(tmp_path: Path) -> None:
    _, _, catalog = _write_fixture(tmp_path, capabilities=["different_capability"])

    result = CatalogService(catalog).list_operations_for_target("node-01")[0]

    assert result["applicability"]["status"] == "missing_capability"
    assert result["applicability"]["missing_capabilities"] == ["fixture_readonly"]


def test_operation_projection_reports_missing_connector(tmp_path: Path) -> None:
    _, _, catalog = _write_fixture(tmp_path, connector_refs=[])

    result = CatalogService(catalog).list_operations_for_target("node-01")[0]

    assert result["applicability"]["status"] == "missing_connector"
    assert result["applicability"]["missing_connectors"] == ["fixture_api"]


def test_catalog_rejects_duplicate_target_alias(tmp_path: Path) -> None:
    _, _, catalog = _write_fixture(tmp_path)
    data = yaml.safe_load(catalog.read_text())
    data["target_catalog"]["targets"].append(
        dict(data["target_catalog"]["targets"][0])
    )
    catalog.write_text(yaml.safe_dump(data, sort_keys=False))

    with pytest.raises(RExecOpValidationError, match="duplicate target catalog id"):
        CatalogService(catalog)


def test_catalog_rejects_duplicate_yaml_key(tmp_path: Path) -> None:
    _, _, catalog = _write_fixture(tmp_path)
    catalog.write_text(
        "target_catalog:\n"
        '  version: "0.1"\n'
        '  version: "0.1"\n'
        "  targets: []\n"
    )

    with pytest.raises(RExecOpValidationError, match="duplicate catalog key"):
        CatalogService(catalog)


def test_catalog_plan_binds_digests_into_sclite_execution_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, catalog = _write_fixture(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.chdir(runtime)
    controller = OperationController()

    operation = controller.plan(
        profile_path=None,
        environment_path=None,
        catalog_path=catalog,
        intent="observe_status",
        target="node-01",
        mode="dry_run",
    )
    plan = controller.store.load_plan(operation.id)
    intent = build_intent_contract(operation, plan)
    policy = build_policy_decision(operation, plan, intent)
    execution = build_execution_contract(operation, plan, intent, policy)

    assert len(plan.catalog_binding["catalog_digest"]) == 64
    assert len(plan.catalog_binding["profile_digest"]) == 64
    assert execution["catalog_binding"] == plan.catalog_binding
    assert "catalog_path" not in json.dumps(execution)


def test_catalog_drift_blocks_start_before_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, environment, catalog = _write_fixture(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.chdir(runtime)
    controller = OperationController()
    operation = controller.plan(
        profile_path=None,
        environment_path=None,
        catalog_path=catalog,
        intent="observe_status",
        target="node-01",
        mode="dry_run",
    )
    data = yaml.safe_load(environment.read_text())
    data["environment"]["description"] = "drifted after plan"
    environment.write_text(yaml.safe_dump(data, sort_keys=False))
    backend_called = False

    def _unexpected_start(_operation_id: str):
        nonlocal backend_called
        backend_called = True
        raise AssertionError("backend must not be called after catalog drift")

    monkeypatch.setattr(controller.orchestrator, "start", _unexpected_start)

    with pytest.raises(RExecOpValidationError, match="catalog binding drift"):
        controller.start(operation.id)
    assert backend_called is False


def test_operations_unavailable_reports_missing_capability(tmp_path: Path) -> None:
    _, _, catalog = _write_fixture(tmp_path, capabilities=["different_capability"])
    service = CatalogService(catalog)

    result = service.list_unavailable_operations_for_target("node-01")

    assert result["schema"] == "rexecop.operations_unavailable.v0.1"
    assert result["summary"]["unavailable_count"] == 1
    assert result["summary"]["available_count"] == 0
    entry = result["unavailable"][0]
    assert entry["operation_id"] == "observe_status"
    assert entry["status"] == "missing_capability"
    assert "fixture_readonly" in entry["why_unavailable"]
    assert any("capabilities" in option for option in entry["safe_next_options"])
    assert "allowed" not in json.dumps(result)


def test_operations_unavailable_is_empty_when_target_matches(tmp_path: Path) -> None:
    _, _, catalog = _write_fixture(tmp_path)

    result = CatalogService(catalog).list_unavailable_operations_for_target("node-01")

    assert result["unavailable"] == []
    assert result["summary"]["available_count"] == 1


def test_operations_unavailable_filters_by_intent(tmp_path: Path) -> None:
    _, _, catalog = _write_fixture(tmp_path, capabilities=["different_capability"])

    result = CatalogService(catalog).list_unavailable_operations_for_target(
        "node-01",
        intent="observe_status",
    )

    assert len(result["unavailable"]) == 1

    with pytest.raises(RExecOpValidationError, match="unknown profile intent"):
        CatalogService(catalog).list_unavailable_operations_for_target(
            "node-01",
            intent="missing_intent",
        )


def test_catalog_cli_lists_targets_and_applicable_operations(tmp_path: Path) -> None:
    _, _, catalog = _write_fixture(tmp_path)
    runner = CliRunner()

    targets = runner.invoke(app, ["targets", "list", "--catalog", str(catalog)])
    operations = runner.invoke(
        app,
        ["operations", "list", "--catalog", str(catalog), "--target", "node-01"],
    )

    assert targets.exit_code == 0, targets.output
    assert operations.exit_code == 0, operations.output
    assert "192.0.2.67" not in targets.output
    assert str(tmp_path) not in targets.output
    assert '"status": "admission_required"' in operations.output


def test_catalog_cli_lists_unavailable_operations(tmp_path: Path) -> None:
    _, _, catalog = _write_fixture(tmp_path, capabilities=["different_capability"])
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "operations",
            "unavailable",
            "--catalog",
            str(catalog),
            "--target",
            "node-01",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["unavailable_count"] == 1
    assert payload["unavailable"][0]["status"] == "missing_capability"
    assert "192.0.2.67" not in result.stdout


def test_catalog_unknown_target_fails_closed(tmp_path: Path) -> None:
    _, _, catalog = _write_fixture(tmp_path)

    with pytest.raises(RExecOpValidationError, match="unknown catalog target"):
        CatalogService(catalog).resolve_operation("missing", "observe_status")
