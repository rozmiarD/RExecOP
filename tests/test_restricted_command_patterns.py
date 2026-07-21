from __future__ import annotations

from unittest.mock import patch

import pytest

from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest
from rexecop.connectors.command_shape import normalize_allowlisted_argv, normalize_argv
from rexecop.connectors.local_shell import LocalShellReadonlyRuntime


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("sudo", ["id"]),
        ("bash", ["-c", "id"]),
        ("systemctl", ["restart", "zabbix-agent"]),
        ("systemctl", ["--no-pager", "disable", "service"]),
        ("service", ["service", "reload"]),
        ("docker", ["exec", "container", "id"]),
        ("docker", ["restart", "container"]),
        ("docker", ["compose", "up", "-d"]),
        ("docker-compose", ["down"]),
    ],
)
def test_restricted_command_matrix(tool: str, args: list[str]) -> None:
    with pytest.raises(ValueError, match="tool_restricted_pattern"):
        normalize_allowlisted_argv(tool=tool, args=args, allowed_tools=[tool])


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("cat", ["/etc/os-release"]),
        ("df", ["-P", "/"]),
        ("free", ["-m"]),
        ("systemctl", ["is-active", "service"]),
        ("docker", ["ps", "--format", "{{.Names}} {{.Status}}"]),
    ],
)
def test_readonly_command_matrix_remains_allowed(tool: str, args: list[str]) -> None:
    assert normalize_allowlisted_argv(
        tool=tool,
        args=args,
        allowed_tools=[tool],
    ) == [tool, *args]


def test_runtime_owned_normalizer_adds_curl_disable_config_flag() -> None:
    assert normalize_allowlisted_argv(
        tool="curl",
        args=["https://example.invalid"],
        allowed_tools=["curl"],
    ) == ["curl", "-q", "https://example.invalid"]


@pytest.mark.parametrize(
    ("approved_spec", "reason"),
    [
        (False, "tool_not_allowed:curl"),
        (True, "tool_not_allowed_for_approved_spec:curl"),
    ],
)
def test_runtime_owned_normalizer_rejects_unlisted_tools(
    approved_spec: bool,
    reason: str,
) -> None:
    with pytest.raises(ValueError, match=reason):
        normalize_argv(
            "curl",
            (),
            allowed_tools=("cat",),
            contains_tool_restricted_patterns=lambda _tool, _args: (False, ""),
            normalize_tool=lambda value: str(value).strip().lower(),
            approved_spec=approved_spec,
        )


def test_runtime_owned_normalizer_rejects_missing_tool() -> None:
    with pytest.raises(ValueError, match="missing_tool"):
        normalize_argv(
            " ",
            (),
            allowed_tools=(),
            contains_tool_restricted_patterns=lambda _tool, _args: (False, ""),
            normalize_tool=lambda value: str(value).strip().lower(),
        )


def test_restricted_local_shell_never_calls_subprocess() -> None:
    runtime = LocalShellReadonlyRuntime(
        connector_name="host_probe",
        config={
            "allowlist": [
                {
                    "action": "restart_service",
                    "command": "systemctl",
                    "args": ["restart", "service"],
                }
            ]
        },
    )
    with patch("rexecop.connectors.local_shell.subprocess.run") as run_mock:
        response = runtime.invoke(
            ConnectorRequest(
                connector="host_probe",
                action="restart_service",
                target="host",
                mode="dry_run",
            )
        )

    run_mock.assert_not_called()
    assert response.success is False
    assert response.data["error_class"] == connector_errors.VALIDATION_FAILED
    assert "tool_restricted_pattern" in response.error
