from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from helpers.staging_http_server import StagingHttpServer
from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.http_api import HttpApiConnectorRuntime, _RejectRedirects
from rexecop.connectors.http_support import require_same_origin
from rexecop.connectors.local_shell import LocalShellReadonlyRuntime
from rexecop.errors import RExecOpValidationError
from rexecop.evidence.redaction import redact_payload

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = REPO_ROOT / "examples/profiles/runtime-fixture"
pytestmark = pytest.mark.security_regression


def test_http_api_reads_fixture_state_against_staging_server() -> None:
    server = StagingHttpServer()
    server.start()
    try:
        runtime = HttpApiConnectorRuntime(
            connector_name="fixture_source",
            config={
                "base_url": server.base_url,
                "deployment_posture": "fixture",
                "actions": {
                    "read_fixture_state": {
                        "method": "GET",
                        "path": "/fixture/state",
                        "unwrap": "state",
                    },
                },
            },
            profile_root=str(PROFILE_ROOT),
            mutating_allowed=False,
        )
        response = runtime.invoke(
            ConnectorRequest(
                connector="fixture_source",
                action="read_fixture_state",
                target="fixture-target",
                mode="dry_run",
            )
        )
        assert response.success
        assert response.data["observed"] is True
        assert response.data["status"] == "ready"
        assert response.data["observed_destination_binding"]["address_class"] == "loopback"
    finally:
        server.stop()


def test_http_api_blocks_undeclared_capability() -> None:
    runtime = HttpApiConnectorRuntime(
        connector_name="fixture_source",
        config={
            "base_url": "http://127.0.0.1:9",
            "actions": {"restart": {"method": "POST", "path": "/restart", "mutating": True}},
        },
        profile_root=str(PROFILE_ROOT),
        mutating_allowed=True,
    )
    response = runtime.invoke(
        ConnectorRequest(connector="fixture_source", action="delete", target="t", mode="apply")
    )
    assert not response.success
    assert response.data["error_class"] == connector_errors.CAPABILITY_UNDECLARED


def test_http_api_blocks_mutating_without_governance() -> None:
    server = StagingHttpServer()
    server.start()
    try:
        runtime = HttpApiConnectorRuntime(
            connector_name="fixture_source",
            config={
                "base_url": server.base_url,
                "actions": {
                    "apply_fixture_change": {
                        "method": "POST",
                        "path": "/fixture/change",
                        "mutating": True,
                        "body": {},
                    }
                },
            },
            profile_root=str(PROFILE_ROOT),
            mutating_allowed=False,
        )
        response = runtime.invoke(
            ConnectorRequest(
                connector="fixture_source",
                action="apply_fixture_change",
                target="t",
                mode="apply",
            )
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

    assert not response.success
    assert response.data["stdout"] == "abcd"
    assert response.data["output_truncated"]["stdout"] is True
    assert response.data["output_sizes"]["stdout_bytes"] == 6
    assert response.data["output_digests"]["stdout"].startswith("sha256:")
    assert response.data["error_class"] == "output_limit_exceeded"


def test_local_shell_mock_combined_output_exceeding_limit_is_failure() -> None:
    runtime = LocalShellReadonlyRuntime(
        connector_name="host_probe",
        config={
            "max_output_bytes": 4,
            "allowlist": [{"action": "probe", "command": "printf"}],
        },
    )

    class Result:
        returncode = 0
        stdout = "ab"
        stderr = "cde"

    with patch("rexecop.connectors.local_shell.subprocess.run", return_value=Result()):
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_probe",
                action="probe",
                target="local",
                mode="dry_run",
            )
        )

    assert response.success is False
    assert response.data["error_class"] == "output_limit_exceeded"
    assert response.data["stdout"] == "ab"
    assert response.data["stderr"] == "cd"
    assert response.data["output_sizes"]["total_bytes"] == 5


@pytest.mark.parametrize(
    ("configured", "policy"),
    [
        (-1, None),
        (0, None),
        (True, None),
        (1.0, None),
        ("1", None),
        (float("nan"), None),
        (float("inf"), None),
        (64, -1),
        (64, 0),
        (64, True),
        (64, 1.0),
        (64, "1"),
        (64, float("nan")),
        (64, float("inf")),
    ],
)
def test_local_shell_rejects_nonpositive_output_limit_before_launch(
    configured: object,
    policy: object | None,
) -> None:
    runtime = LocalShellReadonlyRuntime(
        connector_name="host_probe",
        config={
            "max_output_bytes": configured,
            "allowlist": [{"action": "probe", "command": "printf"}],
        },
    )
    metadata = (
        {"execution_controls": {"max_output_bytes": policy}}
        if policy is not None
        else {}
    )

    with patch("rexecop.connectors.local_shell.subprocess.run") as backend:
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_probe",
                action="probe",
                target="local",
                mode="dry_run",
                metadata=metadata,
            )
        )

    backend.assert_not_called()
    assert response.success is False
    assert response.data["error_class"] == connector_errors.VALIDATION_FAILED
    assert response.error == "invalid max_output_bytes"


@pytest.mark.parametrize("limit", [1, 1024 * 1024])
def test_local_shell_accepts_positive_output_limits(limit: int) -> None:
    runtime = LocalShellReadonlyRuntime(
        connector_name="host_probe",
        config={
            "max_output_bytes": limit,
            "allowlist": [{"action": "probe", "command": "printf"}],
        },
    )

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    with patch(
        "rexecop.connectors.local_shell.subprocess.run",
        return_value=Result(),
    ) as backend:
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_probe",
                action="probe",
                target="local",
                mode="dry_run",
            )
        )

    assert response.success is True
    assert backend.call_args.kwargs["max_output_bytes"] == limit


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

        def read(self, _size: int = -1) -> bytes:
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
    with patch(
        "rexecop.connectors.http_api.HttpApiConnectorRuntime._open_url",
        return_value=Response(),
    ):
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


def test_http_api_rejects_oversized_success_payload_before_parsing() -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size: int = -1) -> bytes:
            return b"x" * size

    runtime = HttpApiConnectorRuntime(
        connector_name="api",
        config={
            "base_url": "https://api.example",
            "max_response_bytes": 16,
            "actions": {"probe": {"method": "GET", "path": "/probe"}},
        },
        profile_root=None,
        mutating_allowed=False,
    )
    with patch(
        "rexecop.connectors.http_api.HttpApiConnectorRuntime._open_url",
        return_value=Response(),
    ):
        response = runtime.invoke(
            ConnectorRequest(
                connector="api",
                action="probe",
                target="target",
                mode="dry_run",
            )
        )

    assert response.success is False
    assert response.data["error_class"] == connector_errors.VALIDATION_FAILED
    assert response.data["output_truncated"] is True


def test_http_api_uses_operator_managed_ca_file_for_verified_tls() -> None:
    class Resolver:
        def resolve(self, secret_ref: str) -> str:
            assert secret_ref == "fixture_ca_file"
            return "/operator/ca/fixture.pem"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, _size: int = -1) -> bytes:
            return b'{"status":"ok"}'

    runtime = HttpApiConnectorRuntime(
        connector_name="api",
        config={
            "base_url": "https://localhost:9443",
            "tls": {"ca_file_secret_ref": "fixture_ca_file"},
            "actions": {"probe": {"method": "GET", "path": "/status"}},
        },
        profile_root=None,
        mutating_allowed=False,
        secret_resolver=Resolver(),
    )
    context = object()
    with (
        patch(
            "rexecop.connectors.http_api.ssl.create_default_context",
            return_value=context,
        ) as create_context,
        patch(
            "rexecop.connectors.http_api.HttpApiConnectorRuntime._open_url",
            return_value=Response(),
        ) as urlopen,
    ):
        response = runtime.invoke(
            ConnectorRequest(
                connector="api",
                action="probe",
                target="target",
                mode="dry_run",
            )
        )

    assert response.success is True
    create_context.assert_called_once_with(cafile="/operator/ca/fixture.pem")
    assert urlopen.call_args.kwargs["context"] is context


def test_http_api_rejects_insecure_or_unknown_tls_options() -> None:
    runtime = HttpApiConnectorRuntime(
        connector_name="api",
        config={
            "base_url": "https://api.example",
            "tls": {"verify": False},
            "actions": {"probe": {"method": "GET", "path": "/probe"}},
        },
        profile_root=None,
        mutating_allowed=False,
    )

    response = runtime.invoke(
        ConnectorRequest(
            connector="api",
            action="probe",
            target="target",
            mode="dry_run",
        )
    )

    assert response.success is False
    assert response.data["error_class"] == connector_errors.VALIDATION_FAILED
    assert "unsupported fields" in str(response.error)


def test_http_transport_rejects_automatic_redirect_hop() -> None:
    handler = _RejectRedirects()
    with pytest.raises(RExecOpValidationError, match="redirect blocked"):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/")


@pytest.mark.parametrize(
    "candidate",
    [
        "https://api.example:8443/items",
        "http://api.example/items",
        "https://other.example/items",
    ],
)
def test_http_origin_rejects_port_host_and_scheme_drift(candidate: str) -> None:
    with pytest.raises(ValueError, match="unsafe_destination"):
        require_same_origin("https://api.example/items", candidate)


def test_stable_http_rejects_plaintext_before_io() -> None:
    runtime = HttpApiConnectorRuntime(
        connector_name="fixture_source",
        config={
            "base_url": "http://api.example",
            "actions": {
                "read_fixture_state": {"method": "GET", "path": "/fixture/state"}
            },
        },
        profile_root=str(PROFILE_ROOT),
        mutating_allowed=False,
    )
    with patch.object(runtime, "_open_url") as backend:
        response = runtime.invoke(
            ConnectorRequest(
                connector="fixture_source",
                action="read_fixture_state",
                target="t",
                mode="dry_run",
            )
        )
    assert response.success is False
    assert "requires https" in response.error
    backend.assert_not_called()


def test_stable_dns_http_requires_operator_egress_control_before_io() -> None:
    runtime = HttpApiConnectorRuntime(
        connector_name="fixture_source",
        config={
            "base_url": "https://api.example",
            "actions": {
                "read_fixture_state": {"method": "GET", "path": "/fixture/state"}
            },
        },
        profile_root=str(PROFILE_ROOT),
        mutating_allowed=False,
    )
    with patch.object(runtime, "_open_url") as backend:
        response = runtime.invoke(
            ConnectorRequest(
                connector="fixture_source",
                action="read_fixture_state",
                target="t",
                mode="dry_run",
            )
        )
    assert response.success is False
    assert "DNS rebinding controls" in response.error
    backend.assert_not_called()


def test_resolved_http_destination_must_match_declared_binding_before_io() -> None:
    class Resolver:
        def resolve(self, secret_ref: str) -> str:
            assert secret_ref == "base"
            return "https://actual.example"

    runtime = HttpApiConnectorRuntime(
        connector_name="api",
        config={
            "base_url_secret_ref": "base",
            "destination_binding": {
                "scheme": "https",
                "effective_port": 443,
                "address_class": "dns_name",
                "origin_binding_digest": "sha256:" + "0" * 64,
            },
            "actions": {"probe": {"method": "GET", "path": "/probe"}},
        },
        profile_root=None,
        mutating_allowed=False,
        secret_resolver=Resolver(),
    )
    with patch.object(runtime, "_open_url") as backend:
        response = runtime.invoke(
            ConnectorRequest(connector="api", action="probe", target="t", mode="dry_run")
        )
    assert response.success is False
    assert "binding drift" in response.error
    backend.assert_not_called()


def test_http_api_blocks_cross_origin_pagination_before_request() -> None:
    runtime = HttpApiConnectorRuntime(
        connector_name="api",
        config={
            "base_url": "https://api.example",
            "actions": {
                "list": {
                    "method": "GET",
                    "path": "/items",
                    "pagination": {"items_path": "items", "next_path": "next"},
                }
            },
        },
        profile_root=None,
        mutating_allowed=False,
    )
    with patch.object(
        runtime,
        "_fetch_json",
        return_value=(
            type("Response", (), {"success": True})(),
            {"items": [], "next": "https://evil.example/items"},
            "https://api.example/items",
        ),
    ) as fetch:
        response = runtime.invoke(
            ConnectorRequest(connector="api", action="list", target="t", mode="dry_run")
        )
    assert response.success is False
    assert "unsafe" in str(response.error)
    assert fetch.call_count == 1


def test_http_api_rejects_pagination_loop() -> None:
    runtime = HttpApiConnectorRuntime(
        connector_name="api",
        config={
            "base_url": "https://api.example",
            "actions": {
                "list": {
                    "method": "GET",
                    "path": "/items",
                    "pagination": {
                        "items_path": "items",
                        "next_path": "next",
                        "max_pages": 5,
                    },
                }
            },
        },
        profile_root=None,
        mutating_allowed=False,
    )
    successful = type("Response", (), {"success": True})()
    with patch.object(
        runtime,
        "_fetch_json",
        return_value=(
            successful,
            {"items": [], "next": "https://api.example/items?page=2"},
            "https://api.example/items",
        ),
    ) as fetch:
        response = runtime.invoke(
            ConnectorRequest(connector="api", action="list", target="t", mode="dry_run")
        )
    assert response.success is False
    assert response.error == "pagination loop detected"
    assert fetch.call_count == 2


def test_http_api_bounds_pagination_page_count() -> None:
    runtime = HttpApiConnectorRuntime(
        connector_name="api",
        config={
            "base_url": "https://api.example",
            "actions": {
                "list": {
                    "method": "GET",
                    "path": "/items",
                    "pagination": {
                        "items_path": "items",
                        "next_path": "next",
                        "max_pages": 2,
                    },
                }
            },
        },
        profile_root=None,
        mutating_allowed=False,
    )
    successful = type("Response", (), {"success": True})()
    pages = iter(
        [
            (successful, {"items": [1], "next": "/items?page=2"}, "https://api.example/items"),
            (successful, {"items": [2], "next": "/items?page=3"}, "https://api.example/items?page=2"),
        ]
    )
    with patch.object(runtime, "_fetch_json", side_effect=lambda *_: next(pages)) as fetch:
        response = runtime.invoke(
            ConnectorRequest(connector="api", action="list", target="t", mode="dry_run")
        )
    assert response.success is True
    assert response.data["items"] == [1, 2]
    assert fetch.call_count == 2


def test_http_api_rejects_transport_reserved_auth_header_before_io() -> None:
    class Resolver:
        def resolve(self, _secret_ref: str) -> str:
            return "fixture-secret"

    runtime = HttpApiConnectorRuntime(
        connector_name="api",
        config={
            "base_url": "https://api.example",
            "auth": {"secret_ref": "auth", "header": "Host"},
            "actions": {"probe": {"method": "GET", "path": "/probe"}},
        },
        profile_root=None,
        mutating_allowed=False,
        secret_resolver=Resolver(),
    )
    with patch("rexecop.connectors.http_api.HttpApiConnectorRuntime._open_url") as backend:
        response = runtime.invoke(
            ConnectorRequest(connector="api", action="probe", target="t", mode="dry_run")
        )
    assert response.success is False
    assert "transport-reserved" in str(response.error)
    backend.assert_not_called()
