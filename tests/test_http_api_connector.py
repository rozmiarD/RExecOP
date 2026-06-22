from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
    assert response.data["output_digests"]["stdout"].startswith("sha256:")
    assert response.data["output_truncated"]["stdout"] is False


def test_local_shell_readonly_bounds_output_and_keeps_digest() -> None:
    runtime = LocalShellReadonlyRuntime(
        connector_name="host_probe",
        config={
            "max_output_bytes": 4,
            "allowlist": [
                {"action": "probe", "command": "printf", "args": ["abcdef"]},
            ],
        },
    )

    class Result:
        returncode = 0
        stdout = "abcdef"
        stderr = ""

    with patch("rexecop.connectors.local_shell.subprocess.run", return_value=Result()):
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_probe",
                action="probe",
                target="local",
                mode="dry_run",
            )
        )

    assert response.success
    assert response.data["stdout"] == "abcd"
    assert response.data["output_truncated"]["stdout"] is True
    assert response.data["output_sizes"]["stdout_bytes"] == 6
    assert response.data["output_digests"]["stdout"].startswith("sha256:")


def test_local_shell_redacts_plaintext_secret_from_output_and_error() -> None:
    runtime = LocalShellReadonlyRuntime(
        connector_name="host_probe",
        config={"allowlist": [{"action": "probe", "command": "printf"}]},
    )

    class Result:
        returncode = 1
        stdout = "token=fixture-output-secret"
        stderr = "password=fixture-error-secret"

    with patch("rexecop.connectors.local_shell.subprocess.run", return_value=Result()):
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_probe",
                action="probe",
                target="local",
                mode="dry_run",
            )
        )

    serialized = str(response.as_dict())
    assert "fixture-output-secret" not in serialized
    assert "fixture-error-secret" not in serialized
    assert "[REDACTED]" in serialized


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


def test_http_api_redacts_resolved_secret_echoed_under_neutral_key() -> None:
    secret = "fixture-resolved-http-secret"

    class Resolver:
        def resolve(self, secret_ref: str) -> str:
            assert secret_ref == "api_auth"
            return secret

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self) -> bytes:
            return ('{"value":"' + secret + '"}').encode()

    runtime = HttpApiConnectorRuntime(
        connector_name="api",
        config={
            "base_url": "https://api.example",
            "auth": {"secret_ref": "api_auth"},
            "actions": {"probe": {"method": "GET", "path": "/probe"}},
        },
        profile_root=None,
        mutating_allowed=False,
        secret_resolver=Resolver(),
    )
    with patch("rexecop.connectors.http_api.urllib.request.urlopen", return_value=Response()):
        response = runtime.invoke(
            ConnectorRequest(
                connector="api",
                action="probe",
                target="target",
                mode="dry_run",
            )
        )
    assert response.success
    assert secret not in str(response.as_dict())
    assert response.data["value"] == "[REDACTED]"
