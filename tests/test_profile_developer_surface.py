from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.profile.conformance import CONFORMANCE_CATEGORIES, validate_profile_conformance
from rexecop.profile.discoverability import (
    list_capabilities_manifest,
    list_connectors_manifest,
    run_profile_developer_check,
    show_connector_manifest,
    show_profile_manifest,
)
from rexecop.profile.extension_manifest import (
    EXTENSION_MANIFEST_SCHEMA,
    build_extension_manifest,
    build_plugin_compatibility_report,
)

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "examples/profiles/runtime-fixture/profile.yaml"
REGISTERED_PROFILE = "tecrax"

runner = CliRunner()


def test_extension_manifest_includes_core_extensions() -> None:
    manifest = build_extension_manifest()

    assert manifest["schema"] == EXTENSION_MANIFEST_SCHEMA
    assert manifest["compatibility_version"]
    assert "profile_contract" in manifest["required_contracts"]
    assert "connector_contract" in manifest["required_contracts"]
    assert manifest["supported_tracks"] == ["readonly", "mutation", "all"]
    backend_classes = {item["backend_class"] for item in manifest["connector_backends"]}
    assert "http_api" in backend_classes
    assert "record_rollback_marker" in {
        item["name"] for item in manifest["internal_actions"]
    }
    assert manifest["digest"]


def test_plugin_compatibility_report_passes_for_builtin_stack() -> None:
    report = build_plugin_compatibility_report()

    assert report["status"] == "passed"
    assert report["connector_backends"] == [] or all(
        item["status"] == "passed" for item in report["connector_backends"]
    )


def test_profile_conformance_reports_categories_for_tecrax() -> None:
    result = validate_profile_conformance(REGISTERED_PROFILE, track="readonly")

    assert result.status == "passed"
    categories = dict(result.categories)
    assert set(categories) == set(CONFORMANCE_CATEGORIES)
    assert categories["catalog"].status == "passed"
    assert categories["readonly"].status == "passed"
    assert categories["validation"].status == "passed"
    assert "categories" in result.as_dict()


def test_profile_developer_check_runs_without_runtime_store() -> None:
    result = run_profile_developer_check(REGISTERED_PROFILE, track="readonly")

    assert result["requires_runtime_store"] is False
    assert result["status"] == "passed"
    assert result["conformance"]["categories"]["readonly"]["status"] == "passed"


def test_show_profile_manifest_includes_intents_and_compatibility() -> None:
    result = show_profile_manifest(REGISTERED_PROFILE)

    assert result["profile"]["name"] == "tecrax"
    assert result["profile"]["intents"]
    assert result["compatibility"]["readonly"] == "passed"
    assert result["developer_check"]["status"] == "passed"


def test_runtime_fixture_reports_catalog_errors_without_crashing() -> None:
    result = show_profile_manifest(PROFILE)

    assert result["profile"]["catalog_errors"]
    assert result["compatibility"]["readonly"] == "failed"


def test_connectors_list_includes_core_backends() -> None:
    result = list_connectors_manifest()

    backend_classes = {item["backend_class"] for item in result["connector_backends"]}
    assert {"http_api", "ssh_readonly", "local_shell_readonly"}.issubset(backend_classes)


def test_connectors_show_describes_http_api_backend() -> None:
    result = show_connector_manifest("http_api")

    assert result["connector_backend"]["backend_class"] == "http_api"
    assert result["connector_backend"]["certification_tier"] == "core"
    assert "connector.http.rest.read" in result["connector_backend"]["capability_descriptors"]


def test_capabilities_list_includes_connector_and_internal_sources() -> None:
    result = list_capabilities_manifest()
    capabilities = {item["capability"]: item["source"] for item in result["capabilities"]}

    assert capabilities["connector.http.rest.read"] == "rexecop.core"
    assert capabilities["internal.record_rollback_marker"] == "rexecop.core"
    assert capabilities["secret.env"] == "rexecop.core"


@pytest.mark.parametrize(
    "command",
    [
        ["profiles", "list"],
        ["profiles", "show", REGISTERED_PROFILE],
        ["connectors", "list"],
        ["connectors", "show", "http_api"],
        ["capabilities", "list"],
        ["profile", "manifest"],
    ],
)
def test_developer_surface_cli_emits_json(command: list[str]) -> None:
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)