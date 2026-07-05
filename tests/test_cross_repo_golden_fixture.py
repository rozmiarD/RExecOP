from __future__ import annotations

import json
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.operation.controller import OperationController
from rexecop.operation.state import OperationState
from rexecop.reaction.service import ReactionService
from rexecop.storage.file_store import FileStore
from rexecop.truth_path import project_truth_path

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
AVAILABLE_UPDATES_SUMMARY = "/usr/lib/update-notifier/apt-check"


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


def _write_fixture_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text(
        "secrets:\n"
        "  monitoring_host_ssh_identity: /tmp/test-identity\n"
        "  portainer_base_url: https://localhost:19443\n"
        "  portainer_ca_file: /tmp/fixture-portainer-ca.pem\n",
        encoding="utf-8",
    )
    secrets_path.chmod(0o600)
    monkeypatch.setenv("REXECOP_SECRETS_FILE", str(secrets_path))


def _run_tecrax_golden_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[OperationController, str, str]:
    _write_fixture_secrets(tmp_path, monkeypatch)
    outputs = {
        "cat /etc/os-release": 'PRETTY_NAME="Ubuntu 24.04 LTS"\nID=ubuntu\nVERSION_ID="24.04"\n',
        "uname -srm": "Linux 6.8.0 x86_64\n",
        "hostname": "monitoring-host\n",
        "uptime": "up 5 days\n",
        "cat /proc/loadavg": "0.10 0.20 0.30 1/200 12345\n",
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
        "systemctl is-enabled unattended-upgrades": "enabled\n",
        AVAILABLE_UPDATES_SUMMARY: "0;0\n",
        "sysctl -n kernel.randomize_va_space": "2\n",
        "sysctl -n kernel.dmesg_restrict": "1\n",
        "find /var/run -maxdepth 1 -name reboot-required -printf '%f\\n'": "",
        "ntpq -c 'rv 0 stratum,offset,rootdelay,rootdisp,leap'": (
            "stratum=3, offset=0.123, rootdelay=1.23, rootdisp=2.34, leap=0\n"
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
            auto_react="plan_only",
        )
        completed = controller.start(operation.id)
        controller.export_receipt(operation.id)

    assert completed.state == OperationState.COMPLETED.value
    stored = controller.get_operation(operation.id)
    auto_reaction = stored.metadata["auto_reaction"]
    return controller, operation.id, str(auto_reaction["reaction_id"])


@pytest.mark.delivery
def test_cross_repo_golden_fixture_explain_and_replay_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, operation_id, reaction_id = _run_tecrax_golden_flow(tmp_path, monkeypatch)
    operation = controller.get_operation(operation_id)
    plan = controller.store.load_plan(operation_id)
    truth_path = project_truth_path(operation, plan)
    reaction = ReactionService(controller).explain(reaction_id)
    replay = ReactionService(controller).replay(reaction_id)

    assert truth_path["auto_reaction"]["reaction_id"] == reaction_id
    assert truth_path["governance_trace"]["trace_digest"].startswith("sha256:")
    assert reaction["schema"] == "rexecop.reaction_explain.v0.1"
    assert reaction["status"] == "verified"
    assert replay["status"] == "passed"
    assert reaction["chain"]["root_digest"].startswith("sha256:")
    assert reaction["automation_chain"]["status"] == "passed"

    repeated = ReactionService(controller).plan(
        profile_path="tecrax",
        environment_path=HOST_INVENTORY_ENVIRONMENT,
        source_operation_id=operation_id,
        target="monitoring-host-01",
        mode="dry_run",
    )
    assert repeated["reaction_id"] == reaction_id
    assert repeated["idempotent_replay"] is True
    assert repeated["reaction_plan"]["child_operation_id"] == reaction["child_operation_id"]

    runner = CliRunner()
    root = controller.store.root
    reaction_result = runner.invoke(
        app,
        ["--root", str(root), "reaction", "explain", "--reaction", reaction_id],
    )
    assert reaction_result.exit_code == 0, reaction_result.output
    reaction_payload = json.loads(reaction_result.stdout)
    assert reaction_payload["schema"] == "rexecop.reaction_explain.v0.1"
    assert reaction_payload["status"] == "verified"

    chain_result = runner.invoke(app, ["--root", str(root), "chain", "explain", operation_id])
    assert chain_result.exit_code == 0, chain_result.output
    chain_payload = json.loads(chain_result.stdout)
    assert chain_payload["schema"] == "rexecop.chain_explain.v0.1"
    assert chain_payload["reaction"]["reaction_id"] == reaction_id
    assert chain_payload["reaction"]["status"] == "verified"
    assert chain_payload["reaction"]["replay_status"] == "passed"
    assert chain_payload["reaction"]["automation_chain"]["status"] == "passed"
