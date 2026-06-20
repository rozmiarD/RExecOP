from __future__ import annotations

from unittest.mock import patch

from helpers.staging_http_server import StagingHttpServer
from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.http_api import HttpApiConnectorRuntime
from rexecop.connectors.http_support import (
    http_error_class,
    merge_paginated_items,
    read_http_error_body,
    resolve_retry_config,
    retry_delay_seconds,
)
from rexecop.connectors.ssh_readonly import SshReadonlyRuntime


def test_retry_delay_uses_configured_backoff() -> None:
    cfg = resolve_retry_config(
        {"base_delay": 0.2, "max_delay": 1.0},
        None,
    )
    assert retry_delay_seconds(cfg, 0) == 0.2
    assert retry_delay_seconds(cfg, 4) == 1.0


def test_http_api_retries_transient_with_configured_backoff() -> None:
    server = StagingHttpServer()
    server.transient_failures_remaining = 1
    server.start()
    sleeps: list[float] = []
    try:
        runtime = HttpApiConnectorRuntime(
            connector_name="proxmox",
            config={
                "base_url": server.base_url,
                "retry": {
                    "max_attempts": 3,
                    "base_delay": 0.1,
                    "max_delay": 0.3,
                    "on": [connector_errors.TRANSIENT],
                },
                "actions": {
                    "probe": {"method": "GET", "path": "/proxmox/transient"},
                },
            },
            profile_root=None,
            mutating_allowed=False,
        )
        def record_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        with patch(
            "rexecop.connectors.http_api.time.sleep",
            side_effect=record_sleep,
        ):
            response = runtime.invoke(
                ConnectorRequest(
                    connector="proxmox",
                    action="probe",
                    target="all",
                    mode="dry_run",
                )
            )
        assert response.success
        assert sleeps == [0.1]
    finally:
        server.stop()


def test_http_api_pagination_collects_all_pages() -> None:
    server = StagingHttpServer()
    server.start()
    try:
        runtime = HttpApiConnectorRuntime(
            connector_name="proxmox",
            config={
                "base_url": server.base_url,
                "actions": {
                    "list_vms": {
                        "method": "GET",
                        "path": "/proxmox/vms/paged",
                        "pagination": {
                            "items_path": "data.vms",
                            "next_path": "data.next",
                            "max_pages": 5,
                        },
                    },
                },
            },
            profile_root=None,
            mutating_allowed=False,
        )
        response = runtime.invoke(
            ConnectorRequest(
                connector="proxmox",
                action="list_vms",
                target="all",
                mode="dry_run",
            )
        )
        assert response.success
        assert len(response.data["vms"]) == 2
        assert response.data["vms"][0]["id"] == "vm-101"
        assert response.data["vms"][1]["id"] == "vm-102"
    finally:
        server.stop()


def test_http_api_maps_auth_error_with_redacted_body_snippet() -> None:
    server = StagingHttpServer()
    server.start()
    try:
        runtime = HttpApiConnectorRuntime(
            connector_name="proxmox",
            config={
                "base_url": server.base_url,
                "actions": {
                    "list_vms": {"method": "GET", "path": "/proxmox/auth-error"},
                },
            },
            profile_root=None,
            mutating_allowed=False,
        )
        response = runtime.invoke(
            ConnectorRequest(
                connector="proxmox",
                action="list_vms",
                target="all",
                mode="dry_run",
            )
        )
        assert not response.success
        assert response.data["error_class"] == connector_errors.AUTH_FAILED
        assert response.data["status_code"] == 401
        assert "body_snippet" in response.data
        assert "secret-token" not in response.data["body_snippet"]
        assert "[REDACTED]" in response.data["body_snippet"]
    finally:
        server.stop()


def test_http_error_class_mapping() -> None:
    assert http_error_class(401) == connector_errors.AUTH_FAILED
    assert http_error_class(503) == connector_errors.TRANSIENT
    assert http_error_class(404) == connector_errors.VALIDATION_FAILED


def test_merge_paginated_items_uses_leaf_key() -> None:
    assert merge_paginated_items("data.vms", [{"id": "1"}]) == {"vms": [{"id": "1"}]}


def test_read_http_error_body_redacts_json_secrets() -> None:
    class FakeError:
        def read(self) -> bytes:
            return b'{"api_key":"secret-token","message":"denied"}'

    snippet = read_http_error_body(FakeError())
    assert "secret-token" not in snippet
    assert "[REDACTED]" in snippet


def test_ssh_readonly_builds_batch_mode_command() -> None:
    runtime = SshReadonlyRuntime(
        connector_name="host_ro",
        config={
            "host": "pve-01.example.com",
            "user": "readonly",
            "allowlist": [{"action": "uptime", "command": "uptime"}],
        },
    )
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        class Result:
            returncode = 0
            stdout = "up"
            stderr = ""

        return Result()

    with patch("rexecop.connectors.ssh_readonly.subprocess.run", side_effect=fake_run):
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_ro",
                action="uptime",
                target="local",
                mode="dry_run",
            )
        )
    assert response.success
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert argv[0] == "ssh"
    assert "-o" in argv and "BatchMode=yes" in argv
    assert argv[-2] == "readonly@pve-01.example.com"
    assert argv[-1] == "uptime"
