from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from rexecop.connectors.action_shape import validate_http_action_shape
from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.http_api import HttpApiConnectorRuntime
from rexecop.errors import RExecOpValidationError

CONTRACT = {
    "action_shapes": {
        "read_state": {
            "method": "POST",
            "path": "/state",
            "body": {"query": "bounded"},
            "unwrap": "result",
            "mutating": False,
            "max_response_bytes": 1024,
        }
    }
}
CONFIG = {
    "backend": "http_api",
    "base_url": "https://fixture.invalid",
    "max_response_bytes": 1024,
    "actions": {
        "read_state": {
            "method": "POST",
            "path": "/state",
            "body": {"query": "bounded"},
            "unwrap": "result",
        }
    },
}


def test_http_action_shape_has_stable_digest() -> None:
    first = validate_http_action_shape(
        connector_name="api",
        action="read_state",
        connector_contract=CONTRACT,
        connector_config=CONFIG,
    )
    second = validate_http_action_shape(
        connector_name="api",
        action="read_state",
        connector_contract=CONTRACT,
        connector_config=dict(CONFIG),
    )
    assert first == second
    assert first and first.startswith("sha256:")


@pytest.mark.parametrize("field,value", [("method", "DELETE"), ("path", "/mutate")])
def test_http_action_shape_rejects_request_drift(field: str, value: str) -> None:
    config = yaml.safe_load(yaml.safe_dump(CONFIG))
    config["actions"]["read_state"][field] = value
    with pytest.raises(RExecOpValidationError, match="shape mismatch"):
        validate_http_action_shape(
            connector_name="api",
            action="read_state",
            connector_contract=CONTRACT,
            connector_config=config,
        )


def test_http_runtime_rejects_drift_before_backend_io(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    (profile / "connectors").mkdir(parents=True)
    (profile / "profile.yaml").write_text(
        "profile_contract:\n  name: fixture\n  version: '1'\n"
        "  intents: {required: true}\n  workflows: {required: true}\n"
        "  connector_requirements: {required: true}\n  risk_classes: {required: true}\n"
        "  evidence_requirements: {required: true}\n"
        "  governance_expectations: {required: true}\n"
        "  validation_rules: {required: true}\n  escalation_rules: {required: true}\n",
        encoding="utf-8",
    )
    (profile / "connectors" / "api.yaml").write_text(
        yaml.safe_dump({"connector": {"name": "api", "capabilities": ["read_state"], **CONTRACT}}),
        encoding="utf-8",
    )
    config = yaml.safe_load(yaml.safe_dump(CONFIG))
    config["actions"]["read_state"]["path"] = "/mutate"
    runtime = HttpApiConnectorRuntime(
        connector_name="api",
        config=config,
        profile_root=str(profile),
        mutating_allowed=False,
    )
    with patch("rexecop.connectors.http_api.urllib.request.urlopen") as backend:
        response = runtime.invoke(
            ConnectorRequest(connector="api", action="read_state", target="t", mode="dry_run")
        )
    assert response.success is False
    assert response.data["error_class"] == "validation_failed"
    backend.assert_not_called()
