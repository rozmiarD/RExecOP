from __future__ import annotations

import json
import re
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from rexecop.errors import RExecOpValidationError
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.profile.loader import load_profile
from rexecop.profile.resolver import list_registered_profiles, resolve_profile_path
from rexecop.storage.file_store import FileStore
from rexecop.validation.validator import validate_operation_result

REPO_ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = REPO_ROOT / "examples/environments/small-public-unit-proxmox.example.yaml"
FIXTURE_PROFILE = REPO_ROOT / "examples/profiles/tecrax-fixture/profile.yaml"

tecrax = pytest.importorskip("tecrax")

TECRAX_ROOT = Path(tecrax.profile_root()).parents[2]
HOST_INVENTORY_ENVIRONMENT = (
    TECRAX_ROOT / "examples/environments/ubuntu-host.readonly.example.yaml"
)
DOCKER_SERVICE_SHOW = (
    "systemctl show docker --property=LoadState --property=ActiveState "
    "--property=SubState --property=UnitFileState --no-pager"
)
DOCKER_SOCKET_SHOW = (
    "systemctl show docker.socket --property=LoadState --property=ActiveState "
    "--property=SubState --property=UnitFileState --no-pager"
)
ADGUARD_DNS_QUERY = (
    "dig @adguard.example.invalid example.com A +time=2 +tries=1 +noall +answer"
)
ADGUARD_LOGIN_STATUS = (
    "curl -q -sS -m 3 --connect-timeout 2 --max-redirs 0 -o /dev/null "
    "-w %{http_code} http://adguard.example.invalid/login.html"
)


def _ssh_remote_command(argv: object) -> str:
    if isinstance(argv, list) and argv and str(argv[0]) == "ssh":
        return str(argv[-1])
    text = " ".join(str(item) for item in argv) if isinstance(argv, list) else str(argv)
    marker = " readonly-ssh-user@monitoring-host.example.invalid "
    if marker in text:
        return text.split(marker, 1)[1]
    return text


def _local_command(argv: object) -> str:
    return " ".join(str(item) for item in argv) if isinstance(argv, list) else str(argv)


def test_tecrax_profile_entry_point_registered() -> None:
    assert "tecrax" in list_registered_profiles()
    resolved = resolve_profile_path("tecrax")
    profile = load_profile(resolved)
    assert profile.name == "tecrax"
    assert profile.version


def test_core_has_no_domain_specific_tokens() -> None:
    src_root = REPO_ROOT / "src" / "rexecop"
    domain_tokens = {
        "adguard",
        "docker",
        "frigate",
        "hillstone",
        "ntp",
        "pbs",
        "portainer",
        "proxmox",
        "tecrax",
        "ubuntu",
        "zabbix",
    }
    offenders: list[str] = []
    for path in src_root.rglob("*.py"):
        if path.name == "command_shape.py":
            continue
        text = path.read_text().lower()
        if any(
            re.search(rf"\b{re.escape(token)}\b", text)
            for token in domain_tokens
        ):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_tecrax_profile_check_backup_status_e2e(tmp_path: Path) -> None:
    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    operation = controller.plan(
        profile_path="tecrax",
        environment_path=ENVIRONMENT,
        intent="check_backup_status",
        target="all_critical_vms",
        mode="dry_run",
    )
    assert operation.state == OperationState.PLANNED.value
    assert operation.profile == "tecrax"

    completed = controller.start(operation.id)
    assert completed.state == OperationState.COMPLETED.value

    validation = controller.validate(operation.id)
    assert validation["passed"] is True


def test_tecrax_basic_host_inventory_ssh_readonly_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text(
        "secrets:\n  monitoring_host_ssh_identity: /tmp/test-identity\n"
    )
    secrets_path.chmod(0o600)
    monkeypatch.setenv("REXECOP_SECRETS_FILE", str(secrets_path))
    outputs = {
        "cat /etc/os-release": 'PRETTY_NAME="Ubuntu 24.04 LTS"\nID=ubuntu\nVERSION_ID="24.04"\n',
        "uname -srm": "Linux 6.8.0 x86_64\n",
        "hostname": "monitoring-host\n",
        "uptime": "up 2 days\n",
        "cat /proc/loadavg": "0.10 0.20 0.30 1/234 5678\n",
        "df -P /": (
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "/dev/root 100000 9000 91000 9% /\n"
        ),
        "free -m": "Mem: 32000 8000 4000 100 2000 24000\n",
    }

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        command = _ssh_remote_command(argv)
        return subprocess.CompletedProcess(argv, 0, outputs[command], "")

    store = FileStore(tmp_path / ".rexecop")
    controller = OperationController(store=store)
    with patch("rexecop.connectors.ssh_readonly.subprocess.run", side_effect=run):
        operation = controller.plan(
            profile_path="tecrax",
            environment_path=HOST_INVENTORY_ENVIRONMENT,
            intent="collect_basic_host_inventory",
            target="monitoring-host-01",
            mode="dry_run",
        )
        completed = controller.start(operation.id)

    assert completed.state == OperationState.COMPLETED.value, completed.as_dict()
    validation = controller.validate(operation.id)
    assert validation["passed"] is True
    receipt = controller.export_receipt(operation.id)
    assert receipt["review_verdict"] == "pass"
    assert receipt["sclite_refs"]["execution_receipt"]["status"] == "emitted"


def test_tecrax_basic_host_inventory_rejects_apply(tmp_path: Path) -> None:
    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    with pytest.raises(RExecOpValidationError, match="mode apply not declared"):
        controller.plan(
            profile_path="tecrax",
            environment_path=HOST_INVENTORY_ENVIRONMENT,
            intent="collect_basic_host_inventory",
            target="monitoring-host-01",
            mode="apply",
        )


def test_tecrax_ntp_health_ssh_readonly_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text(
        "secrets:\n  monitoring_host_ssh_identity: /tmp/test-identity\n"
    )
    secrets_path.chmod(0o600)
    monkeypatch.setenv("REXECOP_SECRETS_FILE", str(secrets_path))
    outputs = {
        "timedatectl show --property=NTPSynchronized --property=NTP": (
            "NTP=no\nNTPSynchronized=yes\n"
        ),
        "systemctl is-active ntp": "active\n",
        DOCKER_SERVICE_SHOW: (
            "LoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=enabled\n"
        ),
        DOCKER_SOCKET_SHOW: (
            "LoadState=loaded\nActiveState=active\nSubState=listening\nUnitFileState=enabled\n"
        ),
    }

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        command = _ssh_remote_command(argv)
        return subprocess.CompletedProcess(argv, 0, outputs[command], "")

    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    with patch("rexecop.connectors.ssh_readonly.subprocess.run", side_effect=run):
        operation = controller.plan(
            profile_path="tecrax",
            environment_path=HOST_INVENTORY_ENVIRONMENT,
            intent="check_ntp_health",
            target="monitoring-host-01",
            mode="dry_run",
        )
        completed = controller.start(operation.id)

    assert completed.state == OperationState.COMPLETED.value, completed.as_dict()
    assert controller.validate(operation.id)["passed"] is True


def test_tecrax_docker_services_health_ssh_readonly_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text(
        "secrets:\n  monitoring_host_ssh_identity: /tmp/test-identity\n"
    )
    secrets_path.chmod(0o600)
    monkeypatch.setenv("REXECOP_SECRETS_FILE", str(secrets_path))
    outputs = {
        DOCKER_SERVICE_SHOW: (
            "LoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=enabled\n"
        ),
        DOCKER_SOCKET_SHOW: (
            "LoadState=loaded\nActiveState=active\nSubState=listening\nUnitFileState=enabled\n"
        ),
    }

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        command = _ssh_remote_command(argv)
        return subprocess.CompletedProcess(argv, 0, outputs[command], "")

    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    with patch("rexecop.connectors.ssh_readonly.subprocess.run", side_effect=run):
        operation = controller.plan(
            profile_path="tecrax",
            environment_path=HOST_INVENTORY_ENVIRONMENT,
            intent="check_docker_services_health",
            target="monitoring-host-01",
            mode="dry_run",
        )
        completed = controller.start(operation.id)

    assert completed.state == OperationState.COMPLETED.value, completed.as_dict()
    validation = controller.validate(operation.id)
    assert validation["passed"] is True
    assert validation["details"]["scope"] == "systemd_service_only"
    assert validation["details"]["container_runtime_state"] == "not_observed"


def test_tecrax_zabbix_application_health_http_e2e(tmp_path: Path) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, _size: int = -1) -> bytes:
            return json.dumps(
                {"jsonrpc": "2.0", "result": "7.2.14", "id": 1}
            ).encode()

    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    with patch(
        "rexecop.connectors.http_api.urllib.request.urlopen",
        return_value=Response(),
    ):
        operation = controller.plan(
            profile_path="tecrax",
            environment_path=HOST_INVENTORY_ENVIRONMENT,
            intent="check_zabbix_container_health",
            target="monitoring-host-01",
            mode="dry_run",
        )
        completed = controller.start(operation.id)

    assert completed.state == OperationState.COMPLETED.value, completed.as_dict()
    validation = controller.validate(operation.id)
    assert validation["passed"] is True
    assert validation["details"]["container_runtime_state"] == "not_observed"


def test_tecrax_adguard_health_local_shell_e2e(tmp_path: Path) -> None:
    outputs = {
        ADGUARD_DNS_QUERY: (
            "example.com. 300 IN A 104.20.23.154\n"
            "example.com. 300 IN A 172.66.147.243\n"
        ),
        ADGUARD_LOGIN_STATUS: "200",
    }

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        command = _local_command(argv)
        return subprocess.CompletedProcess(argv, 0, outputs[command], "")

    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    with patch("rexecop.connectors.local_shell.subprocess.run", side_effect=run):
        operation = controller.plan(
            profile_path="tecrax",
            environment_path=HOST_INVENTORY_ENVIRONMENT,
            intent="check_adguard_health",
            target="monitoring-host-01",
            mode="dry_run",
        )
        completed = controller.start(operation.id)

    assert completed.state == OperationState.COMPLETED.value, completed.as_dict()
    validation = controller.validate(operation.id)
    assert validation["passed"] is True
    assert validation["details"]["scope"] == "dns_and_web_login_only"
    assert validation["details"]["management_api_state"] == "not_observed"


def test_tecrax_portainer_health_https_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text(
        "secrets:\n"
        "  portainer_base_url: https://localhost:19443\n"
        "  portainer_ca_file: /tmp/fixture-portainer-ca.pem\n"
    )
    secrets_path.chmod(0o600)
    monkeypatch.setenv("REXECOP_SECRETS_FILE", str(secrets_path))

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, _size: int = -1) -> bytes:
            return json.dumps(
                {
                    "Version": "2.33.5",
                    "InstanceID": "fixture-instance-id-must-not-persist",
                }
            ).encode()

    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    with (
        patch(
            "rexecop.connectors.http_api.ssl.create_default_context",
            return_value=object(),
        ),
        patch(
            "rexecop.connectors.http_api.urllib.request.urlopen",
            return_value=Response(),
        ),
    ):
        operation = controller.plan(
            profile_path="tecrax",
            environment_path=HOST_INVENTORY_ENVIRONMENT,
            intent="check_portainer_health",
            target="monitoring-host-01",
            mode="dry_run",
        )
        completed = controller.start(operation.id)

    assert completed.state == OperationState.COMPLETED.value, completed.as_dict()
    validation = controller.validate(operation.id)
    assert validation["passed"] is True
    assert validation["details"]["scope"] == "unauthenticated_status_only"
    assert validation["details"]["api_version"] == "2.33.5"
    assert validation["details"]["management_objects_state"] == "not_observed"
    assert "fixture-instance-id-must-not-persist" not in str(completed.as_dict())
    assert "fixture-instance-id-must-not-persist" not in str(validation)


def test_tecrax_monitoring_diagnosis_preserves_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text(
        "secrets:\n"
        "  monitoring_host_ssh_identity: /tmp/test-identity\n"
        "  portainer_base_url: https://localhost:19443\n"
        "  portainer_ca_file: /tmp/fixture-portainer-ca.pem\n"
    )
    secrets_path.chmod(0o600)
    monkeypatch.setenv("REXECOP_SECRETS_FILE", str(secrets_path))
    outputs = {
        "cat /etc/os-release": 'PRETTY_NAME="Ubuntu 24.04 LTS"\nID=ubuntu\nVERSION_ID="24.04"\n',
        "uname -srm": "Linux 6.8.0 x86_64\n",
        "hostname": "monitoring-host\n",
        "uptime": "up 2 days\n",
        "cat /proc/loadavg": "0.10 0.20 0.30 1/234 5678\n",
        "df -P /": (
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "/dev/root 100000 9000 91000 9% /\n"
        ),
        "free -m": "Mem: 32000 8000 4000 100 2000 24000\n",
        "timedatectl show --property=NTPSynchronized --property=NTP": (
            "NTP=no\nNTPSynchronized=yes\n"
        ),
        "systemctl is-active ntp": "active\n",
        DOCKER_SERVICE_SHOW: (
            "LoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=enabled\n"
        ),
        DOCKER_SOCKET_SHOW: (
            "LoadState=loaded\nActiveState=active\nSubState=listening\nUnitFileState=enabled\n"
        ),
    }
    local_outputs = {
        ADGUARD_DNS_QUERY: (
            "example.com. 300 IN A 104.20.23.154\n"
            "example.com. 300 IN A 172.66.147.243\n"
        ),
        ADGUARD_LOGIN_STATUS: "200",
    }

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        local_command = _local_command(argv)
        if local_command in local_outputs:
            return subprocess.CompletedProcess(argv, 0, local_outputs[local_command], "")
        command = _ssh_remote_command(argv)
        return subprocess.CompletedProcess(argv, 0, outputs[command], "")

    controller = OperationController(store=FileStore(tmp_path / ".rexecop"))
    with (
        patch("rexecop.connectors.ssh_readonly.subprocess.run", side_effect=run),
        patch(
            "rexecop.connectors.http_api.urllib.request.urlopen",
            side_effect=urllib.error.URLError("unavailable"),
        ),
        patch(
            "rexecop.connectors.http_api.ssl.create_default_context",
            return_value=object(),
        ),
    ):
        operation = controller.plan(
            profile_path="tecrax",
            environment_path=HOST_INVENTORY_ENVIRONMENT,
            intent="diagnose_monitoring_host",
            target="monitoring-host-01",
            mode="dry_run",
        )
        completed = controller.start(operation.id)

    assert completed.state == OperationState.COMPLETED.value, completed.as_dict()
    validation = controller.validate(operation.id)
    assert validation["passed"] is True
    details = validation["details"]
    assert details["diagnostic_complete"] is True
    assert details["observed_health"] == "degraded"
    assert details["components"]["docker"]["status"] == "healthy"
    assert details["components"]["zabbix"]["status"] == "unhealthy"
    assert details["components"]["adguard"]["status"] == "healthy"
    assert details["components"]["portainer"]["status"] == "unhealthy"
    assert details["continued_failures"] == [
        {
            "step_id": "read_portainer_status",
            "error_class": "transient_connector_error",
        },
        {
            "step_id": "read_zabbix_api_version",
            "error_class": "transient_connector_error",
        }
    ]


def test_validator_requires_profile_root_for_unknown_intent() -> None:
    profile = load_profile(FIXTURE_PROFILE)
    try:
        validate_operation_result(
            intent="unknown_intent",
            shared_state={},
            profile=profile,
        )
    except Exception as exc:
        assert "no validation rules" in str(exc)
    else:
        raise AssertionError("expected validation error")
