from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from rexecop.connectors.capability_descriptor import (
    BACKEND_CAPABILITY_DESCRIPTOR_SCHEMA,
    assert_backend_is_declared,
    compile_connector_capability_descriptor,
)
from rexecop.connectors.registry import describe_connector_backend
from rexecop.errors import RExecOpValidationError
from rexecop.execution.executor import StepExecutor
from rexecop.execution.model import (
    TYPED_EXECUTION_BINDING_SCHEMA,
    build_typed_execution_binding,
    execution_receipt_from_results,
    execution_request_from_workflow,
)
from rexecop.execution.typed_spec import (
    COMMAND_EXECUTION_SPEC_SCHEMA,
    HTTP_ACTION_EXECUTION_SPEC_SCHEMA,
    STATIC_FIXTURE_EXECUTION_SPEC_SCHEMA,
    STEP_EXECUTION_SPEC_SCHEMA,
    bind_step_execution_spec,
    compile_step_execution_spec,
    step_execution_spec_digest,
    validate_typed_execution_schema_version,
)
from rexecop.profile.loader import LoadedProfile, load_profile
from rexecop.workflow.runner import WorkflowRunner

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.security_regression
FIXTURE_PROFILE = ROOT / "examples/profiles/runtime-fixture/profile.yaml"
FIXTURE_ENV = ROOT / "examples/environments/runtime-fixture.example.yaml"


class _PluginEntryPoint:
    name = "fixture_plugin"

    def load(self):  # type: ignore[no-untyped-def]
        return lambda **_kwargs: object()


def _plugin_entry_points(**_kwargs: object) -> list[_PluginEntryPoint]:
    return [_PluginEntryPoint()]


def _plugin_profile(
    tmp_path: Path,
    *,
    execution_postures: object,
    network_policy_binding: object | None = None,
) -> LoadedProfile:
    root = tmp_path / "plugin-profile"
    (root / "connectors").mkdir(parents=True)
    connector: dict[str, object] = {
        "name": "plugin_source",
        "required_capability_descriptors": ["connector.plugin.fixture_plugin"],
        "execution_postures": execution_postures,
    }
    if network_policy_binding is not None:
        connector["network_policy_binding"] = network_policy_binding
    (root / "connectors" / "plugin_source.yaml").write_text(
        yaml.safe_dump({"connector": connector}),
        encoding="utf-8",
    )
    return LoadedProfile(root=root, contract={}, name="plugin-profile", version="0.1.0")


def _plugin_step() -> dict[str, str]:
    return {
        "id": "apply_plugin",
        "type": "connector",
        "connector": "plugin_source",
        "action": "apply_change",
    }


PLUGIN_POSTURES = {
    "fixture_only": {
        "live_backend_posture": "fixture_only",
        "allowed_network_egress": "no_network",
    },
    "operator_wrapper": {
        "live_backend_posture": "live_backend",
        "allowed_network_egress": "local_subprocess",
    },
}


def test_connector_backend_descriptor_includes_security_posture() -> None:
    descriptor = describe_connector_backend("http_api")

    assert descriptor.identity_class == "api_token_optional"
    assert descriptor.egress_class == "outbound_http"
    assert descriptor.live_backend_capable is True


def test_compile_http_backend_capability_descriptor() -> None:
    descriptor = compile_connector_capability_descriptor(
        connector="api",
        backend_class="http_api",
        connector_config={
            "enabled": True,
            "backend": "http_api",
            "base_url_secret_ref": "api_base_url",
            "actions": {"read_state": {"method": "GET", "path": "/state"}},
        },
        mode="dry_run",
    )

    assert descriptor["schema"] == BACKEND_CAPABILITY_DESCRIPTOR_SCHEMA
    assert descriptor["egress_class"] == "outbound_http"
    assert descriptor["network_boundary"]["egress"] == "outbound_http"
    assert descriptor["digest"].startswith("sha256:")
    rendered = json.dumps(descriptor, sort_keys=True)
    assert "api_base_url" not in rendered


def test_compile_ssh_capability_descriptor_requires_identity_ref() -> None:
    with pytest.raises(RExecOpValidationError, match="identity_file_secret_ref"):
        compile_connector_capability_descriptor(
            connector="host",
            backend_class="ssh_readonly",
            connector_config={
                "enabled": True,
                "backend": "ssh_readonly",
                "host": "private-host",
                "user": "operator",
                "allowlist": [{"action": "uptime", "command": "uptime", "args": []}],
            },
            mode="dry_run",
        )


def test_assert_backend_is_declared_blocks_raw_shell_backends() -> None:
    with pytest.raises(RExecOpValidationError, match="raw shell backend blocked"):
        assert_backend_is_declared("local_shell")


def test_assert_backend_is_declared_blocks_undeclared_backend_classes() -> None:
    with pytest.raises(RExecOpValidationError, match="undeclared backend capability"):
        assert_backend_is_declared("custom_unknown_backend")


def test_compile_static_fixture_step_execution_spec() -> None:
    env = yaml.safe_load(FIXTURE_ENV.read_text(encoding="utf-8"))
    profile = load_profile(FIXTURE_PROFILE)
    step = {
        "id": "inspect_state",
        "type": "connector",
        "connector": "fixture_source",
        "action": "read_fixture_state",
    }

    spec = compile_step_execution_spec(
        step=step,
        profile=profile,
        connector_config=env["environment"]["connectors"]["fixture_source"],
        mode="dry_run",
    )

    assert spec["schema"] == STEP_EXECUTION_SPEC_SCHEMA
    assert spec["projection_kind"] == "runtime_projection"
    assert spec["digest"].startswith("sha256:")
    assert spec["payload"]["schema"] == STATIC_FIXTURE_EXECUTION_SPEC_SCHEMA
    assert spec["capability_descriptor"]["live_backend_posture"] == "fixture_only"
    assert spec["capability_descriptor"]["egress_class"] == "no_network"
    assert "not a SCLite truth artifact" in spec["non_claims"][0]


@patch(
    "rexecop.connectors.fixture_loader.entry_points",
    side_effect=_plugin_entry_points,
)
def test_plugin_postures_are_explicit_digest_bound_projections(
    _entry_points_mock,
    tmp_path: Path,
) -> None:
    profile = _plugin_profile(tmp_path, execution_postures=PLUGIN_POSTURES)
    fixture = compile_step_execution_spec(
        step=_plugin_step(),
        profile=profile,
        connector_config={
            "enabled": True,
            "backend": "fixture_plugin",
            "execution_posture": "fixture_only",
            "fixture_only": True,
        },
        mode="apply",
    )
    wrapper = compile_step_execution_spec(
        step=_plugin_step(),
        profile=profile,
        connector_config={
            "enabled": True,
            "backend": "fixture_plugin",
            "execution_posture": "operator_wrapper",
            "fixture_only": False,
            "wrapper_command": ["operator-wrapper", "--bounded"],
        },
        mode="apply",
    )

    assert fixture["capability_descriptor"]["identity_class"] == "plugin_declared"
    assert fixture["capability_descriptor"]["secret_ref_requirements"] == []
    assert (
        fixture["capability_descriptor"]["live_backend_posture"],
        fixture["capability_descriptor"]["egress_class"],
    ) == ("fixture_only", "no_network")
    assert fixture["network_policy_binding"] == {
        "allowed_network_egress": ["no_network"]
    }
    assert (
        wrapper["capability_descriptor"]["live_backend_posture"],
        wrapper["capability_descriptor"]["egress_class"],
    ) == ("live_backend", "local_subprocess")
    assert wrapper["network_policy_binding"] == {
        "allowed_network_egress": ["local_subprocess"]
    }
    assert fixture["digest"] != wrapper["digest"]
    assert fixture["capability_descriptor"]["digest"] != wrapper["capability_descriptor"][
        "digest"
    ]


@pytest.mark.parametrize(
    ("declarations", "config", "reason"),
    [
        (None, {"execution_posture": "fixture_only", "fixture_only": True}, "declaration"),
        (PLUGIN_POSTURES, {"fixture_only": True}, "selection"),
        (
            PLUGIN_POSTURES,
            {"execution_posture": "unknown", "fixture_only": True},
            "unsupported plugin execution posture",
        ),
        (
            {"operator_wrapper": PLUGIN_POSTURES["operator_wrapper"]},
            {"execution_posture": "fixture_only", "fixture_only": True},
            "not declared",
        ),
        (
            {
                "fixture_only": {
                    "live_backend_posture": "live_backend",
                    "allowed_network_egress": "no_network",
                }
            },
            {"execution_posture": "fixture_only", "fixture_only": True},
            "contradicts",
        ),
        (
            PLUGIN_POSTURES,
            {"execution_posture": "fixture_only", "fixture_only": False},
            "requires fixture_only true",
        ),
        (
            PLUGIN_POSTURES,
            {
                "execution_posture": "fixture_only",
                "fixture_only": True,
                "wrapper_command": ["wrapper"],
            },
            "forbids wrapper_command",
        ),
        (
            PLUGIN_POSTURES,
            {
                "execution_posture": "fixture_only",
                "fixture_only": True,
                "wrapper_command": [],
            },
            "forbids wrapper_command",
        ),
        (
            PLUGIN_POSTURES,
            {
                "execution_posture": "fixture_only",
                "fixture_only": True,
                "wrapper_command": "",
            },
            "forbids wrapper_command",
        ),
        (
            PLUGIN_POSTURES,
            {
                "execution_posture": "fixture_only",
                "fixture_only": True,
                "wrapper_command": None,
            },
            "forbids wrapper_command",
        ),
        (
            PLUGIN_POSTURES,
            {"execution_posture": "operator_wrapper", "fixture_only": False},
            "requires fixture_only false and wrapper_command",
        ),
        (
            PLUGIN_POSTURES,
            {
                "execution_posture": "operator_wrapper",
                "fixture_only": True,
                "wrapper_command": ["wrapper"],
            },
            "requires fixture_only false and wrapper_command",
        ),
    ],
)
@patch(
    "rexecop.connectors.fixture_loader.entry_points",
    side_effect=_plugin_entry_points,
)
def test_plugin_posture_missing_unknown_or_contradictory_fails_closed(
    _entry_points_mock,
    tmp_path: Path,
    declarations: object,
    config: dict[str, object],
    reason: str,
) -> None:
    profile = _plugin_profile(tmp_path, execution_postures=declarations)
    with pytest.raises(RExecOpValidationError, match=reason):
        compile_step_execution_spec(
            step=_plugin_step(),
            profile=profile,
            connector_config={
                "enabled": True,
                "backend": "fixture_plugin",
                **config,
            },
            mode="apply",
        )


@patch(
    "rexecop.connectors.fixture_loader.entry_points",
    side_effect=_plugin_entry_points,
)
def test_plugin_profile_network_binding_cannot_contradict_selected_posture(
    _entry_points_mock,
    tmp_path: Path,
) -> None:
    profile = _plugin_profile(
        tmp_path,
        execution_postures=PLUGIN_POSTURES,
        network_policy_binding={"allowed_network_egress": ["local_subprocess"]},
    )
    with pytest.raises(RExecOpValidationError, match="contradicts selected execution posture"):
        compile_step_execution_spec(
            step=_plugin_step(),
            profile=profile,
            connector_config={
                "enabled": True,
                "backend": "fixture_plugin",
                "execution_posture": "fixture_only",
                "fixture_only": True,
            },
            mode="apply",
        )


def test_compile_http_action_execution_spec_from_fixture(tmp_path: Path) -> None:
    profile_root = tmp_path / "profile"
    (profile_root / "connectors").mkdir(parents=True)
    (profile_root / "intents").mkdir()
    (profile_root / "workflows").mkdir()
    (profile_root / "validation_rules").mkdir()
    (profile_root / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_contract": {
                    "name": "http_typed_spec",
                    "version": "0.1.0",
                    "intents": {"required": True},
                    "workflows": {"required": True},
                    "connector_requirements": {"required": True},
                    "risk_classes": {"required": True},
                    "evidence_requirements": {"required": True},
                    "governance_expectations": {"required": True},
                    "validation_rules": {"required": True},
                    "escalation_rules": {"required": True},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (profile_root / "connectors" / "api.yaml").write_text(
        yaml.safe_dump(
            {
                "connector": {
                    "name": "api",
                    "backend": "http_api",
                    "capabilities": ["read_state"],
                    "action_shapes": {
                        "read_state": {
                            "method": "GET",
                            "path": "/state",
                            "unwrap": "state",
                            "max_response_bytes": 2048,
                        }
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    connector_config = {
        "enabled": True,
        "backend": "http_api",
        "base_url_secret_ref": "api_base_url",
        "actions": {
            "read_state": {
                "method": "GET",
                "path": "/state",
                "unwrap": "state",
                "max_response_bytes": 2048,
            }
        },
    }
    profile = load_profile(profile_root / "profile.yaml")
    step = {
        "id": "read",
        "type": "connector",
        "connector": "api",
        "action": "read_state",
    }

    spec = compile_step_execution_spec(
        step=step,
        profile=profile,
        connector_config=connector_config,
        mode="dry_run",
    )

    assert spec["payload"]["schema"] == HTTP_ACTION_EXECUTION_SPEC_SCHEMA
    assert spec["payload"]["shape_digest"].startswith("sha256:")
    assert spec["payload"]["shape"]["method"] == "GET"


def test_http_execution_spec_binds_destination_without_raw_host(tmp_path: Path) -> None:
    profile_root = tmp_path / "profile"
    (profile_root / "connectors").mkdir(parents=True)
    for directory in ("intents", "workflows", "validation_rules"):
        (profile_root / directory).mkdir()
    (profile_root / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_contract": {
                    "name": "destination",
                    "version": "0.1.0",
                    **{
                        section: {"required": True}
                        for section in (
                            "intents",
                            "workflows",
                            "connector_requirements",
                            "risk_classes",
                            "evidence_requirements",
                            "governance_expectations",
                            "validation_rules",
                            "escalation_rules",
                        )
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (profile_root / "connectors" / "api.yaml").write_text(
        "connector:\n  name: api\n  backend: http_api\n  capabilities: [read]\n"
        "  action_shapes:\n    read:\n      method: GET\n      path: /state\n",
        encoding="utf-8",
    )
    config = {
        "backend": "http_api",
        "base_url": "https://private-api.example:8443",
        "actions": {"read": {"method": "GET", "path": "/state"}},
    }
    spec = compile_step_execution_spec(
        step={"id": "read", "type": "connector", "connector": "api", "action": "read"},
        profile=load_profile(profile_root / "profile.yaml"),
        connector_config=config,
        mode="dry_run",
    )
    binding = spec["payload"]["destination_binding"]
    assert binding["scheme"] == "https"
    assert binding["effective_port"] == 8443
    assert binding["address_class"] == "dns_name"
    assert binding["origin_binding_digest"].startswith("sha256:")
    assert "private-api.example" not in repr(spec)
    assert spec["capability_descriptor"]["network_boundary"]["destination_binding"] == binding


def test_compile_command_execution_spec_for_readonly_shell(tmp_path: Path) -> None:
    profile_root = tmp_path / "profile"
    (profile_root / "connectors").mkdir(parents=True)
    (profile_root / "intents").mkdir()
    (profile_root / "workflows").mkdir()
    (profile_root / "validation_rules").mkdir()
    (profile_root / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_contract": {
                    "name": "shell_typed_spec",
                    "version": "0.1.0",
                    "intents": {"required": True},
                    "workflows": {"required": True},
                    "connector_requirements": {"required": True},
                    "risk_classes": {"required": True},
                    "evidence_requirements": {"required": True},
                    "governance_expectations": {"required": True},
                    "validation_rules": {"required": True},
                    "escalation_rules": {"required": True},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (profile_root / "connectors" / "host.yaml").write_text(
        yaml.safe_dump(
            {
                "connector": {
                    "name": "host",
                    "backend": "local_shell_readonly",
                    "capabilities": ["uptime"],
                    "command_shapes": {
                        "uptime": {"command": "uptime", "args": ["-p"]}
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    connector_config = {
        "enabled": True,
        "backend": "local_shell_readonly",
        "allowlist": [{"action": "uptime", "command": "uptime", "args": ["-p"]}],
        "max_output_bytes": 4096,
    }
    profile = load_profile(profile_root / "profile.yaml")
    step = {
        "id": "uptime",
        "type": "connector",
        "connector": "host",
        "action": "uptime",
    }

    spec = compile_step_execution_spec(
        step=step,
        profile=profile,
        connector_config=connector_config,
        mode="dry_run",
    )

    assert spec["payload"]["schema"] == COMMAND_EXECUTION_SPEC_SCHEMA
    assert spec["payload"]["argv"] == ["uptime", "-p"]
    assert spec["payload"]["argv_digest"].startswith("sha256:")


def test_step_execution_spec_digest_is_canonical_and_deterministic() -> None:
    env = yaml.safe_load(FIXTURE_ENV.read_text(encoding="utf-8"))
    profile = load_profile(FIXTURE_PROFILE)
    step = {
        "id": "inspect_state",
        "type": "connector",
        "connector": "fixture_source",
        "action": "read_fixture_state",
    }
    kwargs = {
        "step": step,
        "profile": profile,
        "connector_config": env["environment"]["connectors"]["fixture_source"],
        "mode": "dry_run",
    }

    first = compile_step_execution_spec(**kwargs)
    second = compile_step_execution_spec(**kwargs)

    assert first["digest"] == second["digest"]
    assert first["digest"] == step_execution_spec_digest(first)


def test_unknown_typed_execution_schema_major_version_fail_closed() -> None:
    spec = {
        "schema": STEP_EXECUTION_SPEC_SCHEMA,
        "schema_version": "v9.0",
    }

    with pytest.raises(RExecOpValidationError, match="unsupported typed execution schema major"):
        validate_typed_execution_schema_version(spec)


def test_bind_step_execution_spec_detects_drift() -> None:
    env = yaml.safe_load(FIXTURE_ENV.read_text(encoding="utf-8"))
    profile = load_profile(FIXTURE_PROFILE)
    step = {
        "id": "inspect_state",
        "type": "connector",
        "connector": "fixture_source",
        "action": "read_fixture_state",
    }
    spec = compile_step_execution_spec(
        step=step,
        profile=profile,
        connector_config=env["environment"]["connectors"]["fixture_source"],
        mode="dry_run",
    )
    shared_state: dict[str, object] = {}
    bind_step_execution_spec(step_id="inspect_state", spec=spec, shared_state=shared_state)

    drifted = dict(spec)
    drifted["action"] = "mutated_action"
    drifted["digest"] = step_execution_spec_digest(drifted)

    with pytest.raises(RExecOpValidationError, match="typed execution spec drift"):
        bind_step_execution_spec(
            step_id="inspect_state",
            spec=drifted,
            shared_state=shared_state,
        )


def test_workflow_runner_binds_typed_execution_spec_for_fixture_connector() -> None:
    from rexecop.connectors.runtime import ConnectorDispatcher
    from rexecop.connectors.static_fixture import StaticFixtureRuntime

    runtime = StaticFixtureRuntime(
        connector_name="fixture_source",
        mutating_allowed=False,
        config={
            "fixture_only": True,
            "actions": {
                "read_fixture_state": {"data": {"observed": True}},
            },
        },
    )
    executor = StepExecutor(connector_dispatcher=ConnectorDispatcher(runtime))
    env = yaml.safe_load(FIXTURE_ENV.read_text(encoding="utf-8"))
    shared_state = {
        "execution_context": {
            "profile_root": str(FIXTURE_PROFILE),
            "connectors": env["environment"]["connectors"],
        }
    }

    with patch("rexecop.connectors.http_api.HttpApiConnectorRuntime._open_url") as backend:
        result = WorkflowRunner(executor).run(
            operation_id="op-typed-spec",
            target="fixture-target",
            mode="dry_run",
            planned_steps=[
                {
                    "id": "inspect_state",
                    "type": "connector",
                    "connector": "fixture_source",
                    "action": "read_fixture_state",
                }
            ],
            correlation_id="corr-typed-spec",
            shared_state=shared_state,
        )

    backend.assert_not_called()
    assert result.success
    typed_specs = result.shared_state["typed_execution_specs"]
    assert typed_specs["inspect_state"]["digest"].startswith("sha256:")
    assert typed_specs["inspect_state"]["schema"] == STEP_EXECUTION_SPEC_SCHEMA
    assert typed_specs["inspect_state"]["capability_descriptor_digest"].startswith("sha256:")
    receipt = result.shared_state["execution_receipt"]
    binding = receipt["typed_execution_binding"]
    assert binding["schema"] == TYPED_EXECUTION_BINDING_SCHEMA
    assert binding["step_digests"]["inspect_state"]["execution_spec_digest"] == (
        typed_specs["inspect_state"]["digest"]
    )
    assert binding["binding_digest"].startswith("sha256:")
    step_receipt = next(
        item for item in receipt["step_receipts"] if item["step_id"] == "inspect_state"
    )
    assert step_receipt["execution_spec_digest"] == typed_specs["inspect_state"]["digest"]


def test_execution_receipt_binds_policy_and_typed_execution_digests() -> None:
    request = execution_request_from_workflow(
        operation_id="op-bind",
        target="fixture-target",
        mode="dry_run",
        planned_steps=[
            {
                "id": "inspect_state",
                "type": "connector",
                "connector": "fixture_source",
                "action": "read_fixture_state",
            }
        ],
        policy_binding={
            "schema_version": "v0.1",
            "enforcement_plan_id": "plan-1",
            "enforcement_plan_digest": "sha256:" + "a" * 64,
            "admission_id": "adm-1",
            "admission_digest": "sha256:" + "b" * 64,
            "policy_pack_id": "pack-1",
            "policy_pack_version": "1.0.0",
            "policy_pack_digest": "sha256:" + "c" * 64,
            "verdict_id": "verdict-1",
            "verdict_digest": "sha256:" + "d" * 64,
        },
    )
    typed_specs = {
        "inspect_state": {
            "schema": STEP_EXECUTION_SPEC_SCHEMA,
            "digest": "sha256:" + "e" * 64,
            "capability_descriptor_digest": "sha256:" + "f" * 64,
            "admission_digest": "sha256:" + "1" * 64,
            "governance_request_digest": "sha256:" + "2" * 64,
            "destination_binding": {
                "scheme": "https",
                "effective_port": 443,
                "address_class": "dns_name",
                "origin_binding_digest": "sha256:" + "3" * 64,
            },
        }
    }
    receipt = execution_receipt_from_results(
        request=request,
        success=True,
        executed_steps=["inspect_state"],
        step_results={
            "inspect_state": {
                "success": True,
                "output": {
                    "data": {
                        "output_digests": {"record": "sha256:" + "0" * 64},
                        "observed_destination_binding": {
                            "scheme": "https",
                            "effective_port": 443,
                            "address_class": "dns_name",
                            "origin_binding_digest": "sha256:" + "3" * 64,
                        },
                    }
                },
            }
        },
        typed_execution_specs=typed_specs,
    )
    payload = receipt.as_dict()

    assert payload["policy_binding"] == request.policy_binding.as_dict()
    assert payload["typed_execution_binding"]["binding_digest"].startswith("sha256:")
    assert payload["enforcement"]["typed_execution_specs_bound"] is True
    assert build_typed_execution_binding(["inspect_state"], typed_specs) == (
        payload["typed_execution_binding"]
    )
    binding = payload["typed_execution_binding"]["step_digests"]["inspect_state"]
    assert binding["origin_binding_digest"] == "sha256:" + "3" * 64
    assert binding["admission_digest"] == "sha256:" + "1" * 64
    step_receipt = payload["step_receipts"][0]
    assert step_receipt["planned_destination_binding"] == (
        step_receipt["observed_destination_binding"]
    )
