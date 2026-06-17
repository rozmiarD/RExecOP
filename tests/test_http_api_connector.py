from __future__ import annotations

from pathlib import Path

from helpers.staging_http_server import StagingHttpServer
from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.http_api import HttpApiConnectorRuntime
from rexecop.connectors.local_shell import LocalShellReadonlyRuntime
from rexecop.evidence.redaction import redact_payload

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = REPO_ROOT / "examples/profiles/tecrax-fixture"


def test_http_api_list_vms_against_staging_server() -> None:
    server = StagingHttpServer()
    server.start()
    try:
        runtime = HttpApiConnectorRuntime(
            connector_name="proxmox",
            config={
                "base_url": server.base_url,
                "actions": {
                    "list_vms": {"method": "GET", "path": "/proxmox/vms", "unwrap": "vms"},
                },
            },
            profile_root=str(PROFILE_ROOT),
            mutating_allowed=False,
        )
        response = runtime.invoke(
            ConnectorRequest(
                connector="proxmox",
                action="list_vms",
                target="all_critical_vms",
                mode="dry_run",
            )
        )
        assert response.success
        assert response.data["vms"]
    finally:
        server.stop()


def test_http_api_blocks_undeclared_capability() -> None:
    runtime = HttpApiConnectorRuntime(
        connector_name="pbs",
        config={
            "base_url": "http://127.0.0.1:9",
            "actions": {"restart": {"method": "POST", "path": "/restart", "mutating": True}},
        },
        profile_root=str(PROFILE_ROOT),
        mutating_allowed=True,
    )
    response = runtime.invoke(
        ConnectorRequest(connector="pbs", action="restart", target="t", mode="apply")
    )
    assert not response.success
    assert response.data["error_class"] == connector_errors.CAPABILITY_UNDECLARED


def test_http_api_blocks_mutating_without_governance() -> None:
    server = StagingHttpServer()
    server.start()
    try:
        runtime = HttpApiConnectorRuntime(
            connector_name="proxmox",
            config={
                "base_url": server.base_url,
                "actions": {
                    "restart": {
                        "method": "POST",
                        "path": "/proxmox/restart",
                        "mutating": True,
                        "body": {},
                    }
                },
            },
            profile_root=str(PROFILE_ROOT),
            mutating_allowed=False,
        )
        response = runtime.invoke(
            ConnectorRequest(connector="proxmox", action="restart", target="t", mode="apply")
        )
        assert not response.success
        assert response.data["error_class"] == connector_errors.POLICY_DENIED
    finally:
        server.stop()


def test_local_shell_readonly_runs_allowlisted_command() -> None:
    runtime = LocalShellReadonlyRuntime(
        connector_name="host_probe",
        config={
            "allowlist": [
                {"action": "uptime", "command": "uptime"},
            ]
        },
    )
    response = runtime.invoke(
        ConnectorRequest(connector="host_probe", action="uptime", target="local", mode="dry_run")
    )
    assert response.success
    assert "load average" in response.data["stdout"].lower()


def test_api_response_redaction_masks_secret_fields() -> None:
    payload = {
        "vms": [{"name": "vm-1"}],
        "api_key": "secret-token",
        "auth_header": "Bearer abc",
    }
    redacted = redact_payload(payload)
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["auth_header"] == "[REDACTED]"
    assert redacted["vms"][0]["name"] == "vm-1"
