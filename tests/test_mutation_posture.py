from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.connectors.base import ConnectorRequest, ConnectorResponse
from rexecop.connectors.composite_runtime import (
    CompositeConnectorRuntime,
    build_connector_runtime,
)
from rexecop.connectors.runtime import ConnectorDispatcher
from rexecop.errors import RExecOpMutationNotCertified
from rexecop.runtime.doctor import _check_mutation_posture
from rexecop.runtime.mutation_posture import (
    LAB_ONLY_POSTURE,
    STABLE_READ_ONLY_POSTURE,
    require_mutation_execution_enabled,
    resolve_mutation_posture,
)


class _RecordingBackend:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
        self.calls += 1
        return ConnectorResponse(
            connector=request.connector,
            action=request.action,
            success=True,
        )


def _runtime_with_recording_backend() -> tuple[
    CompositeConnectorRuntime,
    _RecordingBackend,
]:
    runtime = build_connector_runtime(
        connectors={"live": {"enabled": True, "backend": "mock"}},
        profile_root=None,
        mutating_allowed=True,
    )
    backend = _RecordingBackend()
    runtime._backends["live"] = backend
    return runtime, backend


def _apply_request() -> ConnectorRequest:
    return ConnectorRequest(
        connector="live",
        action="change",
        target="fixture-target",
        mode="apply",
    )


def test_mutation_posture_defaults_to_stable_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REXECOP_MUTATION_POSTURE", raising=False)

    assert resolve_mutation_posture() == STABLE_READ_ONLY_POSTURE
    with pytest.raises(RExecOpMutationNotCertified) as caught:
        require_mutation_execution_enabled("apply")
    assert caught.value.reason_code == "mutation_not_certified"


def test_invalid_mutation_posture_fails_closed() -> None:
    with pytest.raises(RExecOpMutationNotCertified):
        resolve_mutation_posture("enabled")


def test_composite_runtime_blocks_apply_before_backend_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REXECOP_MUTATION_POSTURE", raising=False)
    runtime, backend = _runtime_with_recording_backend()

    with pytest.raises(RExecOpMutationNotCertified):
        runtime.invoke(_apply_request())

    assert backend.calls == 0


def test_dispatcher_blocks_injected_backend_before_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REXECOP_MUTATION_POSTURE", raising=False)
    backend = _RecordingBackend()

    with pytest.raises(RExecOpMutationNotCertified):
        ConnectorDispatcher(backend).invoke(_apply_request())

    assert backend.calls == 0


def test_lab_only_posture_allows_mutation_mechanics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REXECOP_MUTATION_POSTURE", LAB_ONLY_POSTURE)
    runtime, backend = _runtime_with_recording_backend()

    response = runtime.invoke(_apply_request())

    assert response.success is True
    assert backend.calls == 1


def test_doctor_reports_stable_read_only_as_certified() -> None:
    check = _check_mutation_posture(None)

    assert check["status"] == "passed"
    assert check["details"]["apply_enabled"] is False
    assert check["details"]["certified"] == STABLE_READ_ONLY_POSTURE


def test_cli_doctor_blocks_lab_only_posture(tmp_path: Path) -> None:
    runner = CliRunner()
    root = tmp_path / "runtime"
    assert runner.invoke(app, ["--root", str(root), "init"]).exit_code == 0

    result = runner.invoke(
        app,
        ["--root", str(root), "doctor"],
        env={"REXECOP_MUTATION_POSTURE": LAB_ONLY_POSTURE},
    )

    assert result.exit_code == 1
    assert "mutation_posture" in result.stdout


@pytest.mark.parametrize("value", [LAB_ONLY_POSTURE, "enabled"])
def test_doctor_blocks_nonstable_mutation_posture(value: str) -> None:
    check = _check_mutation_posture(value)

    assert check["status"] == "blocker"
    assert check["id"] == "mutation_posture"
