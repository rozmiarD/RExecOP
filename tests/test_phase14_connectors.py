from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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
from rexecop.connectors.local_shell import LocalShellReadonlyRuntime
from rexecop.connectors.ssh_readonly import SshReadonlyRuntime
from rexecop.execution.bounded_subprocess import BoundedSubprocessResult, CapturedStream

pytestmark = pytest.mark.security_regression


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
            connector_name="fixture_source",
            config={
                "base_url": server.base_url,
                "retry": {
                    "max_attempts": 3,
                    "base_delay": 0.1,
                    "max_delay": 0.3,
                    "on": [connector_errors.TRANSIENT],
                },
                "actions": {
                    "probe": {"method": "GET", "path": "/fixture/transient"},
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
                    connector="fixture_source",
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
            connector_name="fixture_source",
            config={
                "base_url": server.base_url,
                "actions": {
                    "list_items": {
                        "method": "GET",
                        "path": "/fixture/items/paged",
                        "pagination": {
                            "items_path": "data.items",
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
                connector="fixture_source",
                action="list_items",
                target="all",
                mode="dry_run",
            )
        )
        assert response.success
        assert len(response.data["items"]) == 2
        assert response.data["items"][0]["id"] == "fixture-1"
        assert response.data["items"][1]["id"] == "fixture-2"
    finally:
        server.stop()


def test_http_api_maps_auth_error_with_redacted_body_snippet() -> None:
    server = StagingHttpServer()
    server.start()
    try:
        runtime = HttpApiConnectorRuntime(
            connector_name="fixture_source",
            config={
                "base_url": server.base_url,
                "actions": {
                    "read_fixture_state": {
                        "method": "GET",
                        "path": "/fixture/auth-error",
                    },
                },
            },
            profile_root=None,
            mutating_allowed=False,
        )
        response = runtime.invoke(
            ConnectorRequest(
                connector="fixture_source",
                action="read_fixture_state",
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
    assert merge_paginated_items("data.items", [{"id": "1"}]) == {
        "items": [{"id": "1"}]
    }


def test_read_http_error_body_redacts_json_secrets() -> None:
    class FakeError:
        def read(self, _size: int = -1) -> bytes:
            return b'{"api_key":"secret-token","message":"denied"}'

    snippet = read_http_error_body(FakeError())
    assert "secret-token" not in snippet
    assert "[REDACTED]" in snippet


def test_read_http_error_body_redacts_plaintext_assignments() -> None:
    class FakeError:
        def read(self, _size: int = -1) -> bytes:
            return b"request denied token=fixture-secret-value"

    snippet = read_http_error_body(FakeError())
    assert "fixture-secret-value" not in snippet
    assert "[REDACTED]" in snippet


def test_ssh_readonly_builds_batch_mode_command() -> None:
    runtime = SshReadonlyRuntime(
        connector_name="host_ro",
        config={
            "host": "pve-01.example.com",
            "user": "readonly",
            "deployment_posture": "fixture",
            "known_hosts_policy": "accept-new",
            "allowlist": [{"action": "uptime", "command": "uptime"}],
        },
    )
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["kwargs"] = kwargs
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
    assert response.data["output_digests"]["stdout"].startswith("sha256:")
    assert response.data["output_truncated"]["stdout"] is False
    assert captured["argv"] == argv
    assert captured["kwargs"] == {"timeout": 15.0, "max_output_bytes": 65536}


@pytest.mark.parametrize(
    ("configured", "policy"),
    [(0, None), (64, -1), (64, 0)],
)
def test_ssh_rejects_nonpositive_output_limit_before_launch(
    configured: int,
    policy: int | None,
) -> None:
    runtime = SshReadonlyRuntime(
        connector_name="host_ro",
        config={
            "host": "host.example",
            "user": "readonly",
            "deployment_posture": "fixture",
            "known_hosts_policy": "accept-new",
            "max_output_bytes": configured,
            "allowlist": [{"action": "uptime", "command": "uptime"}],
        },
    )
    metadata = (
        {"execution_controls": {"max_output_bytes": policy}}
        if policy is not None
        else {}
    )

    with patch("rexecop.connectors.ssh_readonly.subprocess.run") as backend:
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_ro",
                action="uptime",
                target="fixture",
                mode="dry_run",
                metadata=metadata,
            )
        )

    backend.assert_not_called()
    assert response.success is False
    assert response.data["error_class"] == connector_errors.VALIDATION_FAILED
    assert response.error == "invalid max_output_bytes"


def test_local_shell_output_limit_is_a_failure_not_successful_truncation() -> None:
    runtime = LocalShellReadonlyRuntime(
        connector_name="host_ro",
        config={
            "max_output_bytes": 128,
            "allowlist": [
                {
                    "action": "flood",
                    "command": sys.executable,
                    "args": ["-c", "import os; os.write(1, b'x' * (8 * 1024 * 1024))"],
                }
            ],
        },
    )

    response = runtime.invoke(
        ConnectorRequest(
            connector="host_ro",
            action="flood",
            target="fixture",
            mode="dry_run",
        )
    )

    assert response.success is False
    assert response.data["error_class"] == "output_limit_exceeded"
    assert response.data["output_limit_exceeded"] is True
    assert response.data["output_sizes"]["total_bytes"] > 128
    assert len(response.data["stdout"].encode()) <= 128
    assert response.error == "local shell output limit exceeded"


def test_ssh_output_limit_uses_the_same_failure_classification() -> None:
    runtime = SshReadonlyRuntime(
        connector_name="host_ro",
        config={
            "host": "host.example",
            "user": "readonly",
            "deployment_posture": "fixture",
            "known_hosts_policy": "accept-new",
            "max_output_bytes": 4,
            "allowlist": [{"action": "uptime", "command": "uptime"}],
        },
    )
    stdout = CapturedStream(
        text="abcd",
        digest="sha256:" + "0" * 64,
        total_bytes=5,
        retained_bytes=4,
        truncated=True,
    )
    stderr = CapturedStream(
        text="",
        digest="sha256:" + "0" * 64,
        total_bytes=0,
        retained_bytes=0,
        truncated=False,
    )
    completed = BoundedSubprocessResult(
        args=("ssh",),
        returncode=-15,
        stdout=stdout,
        stderr=stderr,
        output_limit_exceeded=True,
        peak_retained_bytes=4,
    )

    with patch("rexecop.connectors.ssh_readonly.subprocess.run", return_value=completed):
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_ro",
                action="uptime",
                target="fixture",
                mode="dry_run",
            )
        )

    assert response.success is False
    assert response.data["error_class"] == "output_limit_exceeded"
    assert response.data["output_limit_exceeded"] is True
    assert response.error == "ssh command output limit exceeded"


def test_local_shell_preserves_timeout_classification() -> None:
    runtime = LocalShellReadonlyRuntime(
        connector_name="host_ro",
        config={
            "timeout_seconds": 0.05,
            "allowlist": [
                {
                    "action": "wait",
                    "command": sys.executable,
                    "args": ["-c", "import time; time.sleep(30)"],
                }
            ],
        },
    )

    response = runtime.invoke(
        ConnectorRequest(
            connector="host_ro",
            action="wait",
            target="fixture",
            mode="dry_run",
        )
    )

    assert response.success is False
    assert response.data["error_class"] == connector_errors.TIMEOUT
    assert response.error == "local shell timeout"


def test_ssh_readonly_redacts_resolved_identity_path_from_error(tmp_path: Path) -> None:
    identity = tmp_path / "id_runtime"
    identity.write_text("private key", encoding="utf-8")
    identity.chmod(0o600)
    secret_path = str(identity)

    class Resolver:
        def resolve(self, secret_ref: str) -> str:
            assert secret_ref == "ssh_identity"
            return secret_path

    runtime = SshReadonlyRuntime(
        connector_name="host_ro",
        config={
            "host": "host.example",
            "user": "readonly",
            "deployment_posture": "fixture",
            "known_hosts_policy": "accept-new",
            "identity_file_secret_ref": "ssh_identity",
            "allowlist": [{"action": "uptime", "command": "uptime"}],
        },
        secret_resolver=Resolver(),
    )

    class Result:
        returncode = 1
        stdout = ""
        stderr = f"cannot read identity {secret_path}"

    with patch("rexecop.connectors.ssh_readonly.subprocess.run", return_value=Result()):
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_ro",
                action="uptime",
                target="host",
                mode="dry_run",
            )
        )
    assert secret_path not in str(response.as_dict())
    assert "[REDACTED]" in response.error


def test_ssh_readonly_stable_rejects_accept_new_before_io() -> None:
    runtime = SshReadonlyRuntime(
        connector_name="host_ro",
        config={
            "host": "host.example",
            "user": "readonly",
            "known_hosts_policy": "accept-new",
            "allowlist": [{"action": "uptime", "command": "uptime"}],
        },
    )
    with patch("rexecop.connectors.ssh_readonly.subprocess.run") as backend:
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_ro", action="uptime", target="host", mode="dry_run"
            )
        )
    assert not response.success
    assert "requires strict" in response.error
    backend.assert_not_called()


def test_ssh_readonly_rejects_symlinked_known_hosts_before_io(tmp_path: Path) -> None:
    target = tmp_path / "known_hosts.real"
    target.write_text("host key\n", encoding="utf-8")
    link = tmp_path / "known_hosts"
    link.symlink_to(target)
    runtime = SshReadonlyRuntime(
        connector_name="host_ro",
        config={
            "host": "host.example",
            "user": "readonly",
            "known_hosts_file": str(link),
            "allowlist": [{"action": "uptime", "command": "uptime"}],
        },
    )
    with patch("rexecop.connectors.ssh_readonly.subprocess.run") as backend:
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_ro", action="uptime", target="host", mode="dry_run"
            )
        )
    assert not response.success
    assert "non-symlink" in response.error
    backend.assert_not_called()


def test_ssh_readonly_rejects_option_like_destination_before_io() -> None:
    runtime = SshReadonlyRuntime(
        connector_name="host_ro",
        config={
            "host": "-oProxyCommand=bad",
            "user": "readonly",
            "deployment_posture": "fixture",
            "known_hosts_policy": "accept-new",
            "allowlist": [{"action": "uptime", "command": "uptime"}],
        },
    )
    with patch("rexecop.connectors.ssh_readonly.subprocess.run") as backend:
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_ro", action="uptime", target="host", mode="dry_run"
            )
        )
    assert not response.success
    assert "host is malformed" in response.error
    backend.assert_not_called()


def test_ssh_readonly_strict_requires_known_hosts_before_io() -> None:
    runtime = SshReadonlyRuntime(
        connector_name="host_ro",
        config={
            "host": "host.example",
            "user": "readonly",
            "allowlist": [{"action": "uptime", "command": "uptime"}],
        },
    )
    with patch("rexecop.connectors.ssh_readonly.subprocess.run") as backend:
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_ro", action="uptime", target="host", mode="dry_run"
            )
        )
    assert not response.success
    assert "requires known_hosts_file" in response.error
    backend.assert_not_called()


def test_ssh_readonly_rejects_broad_identity_permissions_before_io(tmp_path: Path) -> None:
    identity = tmp_path / "id_runtime"
    identity.write_text("private key", encoding="utf-8")
    identity.chmod(0o644)

    class Resolver:
        def resolve(self, _secret_ref: str) -> str:
            return str(identity)

    runtime = SshReadonlyRuntime(
        connector_name="host_ro",
        config={
            "host": "host.example",
            "user": "readonly",
            "deployment_posture": "fixture",
            "known_hosts_policy": "accept-new",
            "identity_file_secret_ref": "identity",
            "allowlist": [{"action": "uptime", "command": "uptime"}],
        },
        secret_resolver=Resolver(),
    )
    with patch("rexecop.connectors.ssh_readonly.subprocess.run") as backend:
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_ro", action="uptime", target="host", mode="dry_run"
            )
        )
    assert not response.success
    assert "identity file permissions are too broad" in response.error
    backend.assert_not_called()


def test_ssh_readonly_unknown_host_key_fails_closed(tmp_path: Path) -> None:
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("other.example ssh-ed25519 fixture-key\n", encoding="utf-8")
    known_hosts.chmod(0o644)

    class Result:
        returncode = 255
        stdout = ""
        stderr = "Host key verification failed."

    runtime = SshReadonlyRuntime(
        connector_name="host_ro",
        config={
            "host": "host.example",
            "user": "readonly",
            "known_hosts_file": str(known_hosts),
            "allowlist": [{"action": "uptime", "command": "uptime"}],
        },
    )
    with patch(
        "rexecop.connectors.ssh_readonly.subprocess.run", return_value=Result()
    ) as backend:
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_ro", action="uptime", target="host", mode="dry_run"
            )
        )
    assert not response.success
    assert response.error == "Host key verification failed."
    argv = backend.call_args.args[0]
    assert "StrictHostKeyChecking=yes" in argv
