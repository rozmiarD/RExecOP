from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from govengine.governance_decision import _governance_decision_body_digest
from govengine.governance_decision_signing import sign_governance_decision
from govengine.signing import DemoDigestSigner
from govengine.typed_execution_governed_admission import (
    TypedExecutionGovernedAdmissionV02,
)

from rexecop.adapters.govengine_port import runtime_authority as runtime_authority_module
from rexecop.connectors.base import ConnectorRequest, ConnectorResponse
from rexecop.operation.controller import OperationController
from rexecop.storage.file_store import FileStore
from runtime_governance_support import (
    TestApprovalRevocations,
    TestGovernedAttemptAuthority,
    governed_runtime_kwargs,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PROFILE = REPO_ROOT / "examples/profiles/runtime-fixture"
FIXTURE_ENVIRONMENT = REPO_ROOT / "examples/environments/runtime-fixture.example.yaml"


class _PluginRuntime:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
        self.events.append("io")
        return ConnectorResponse(
            connector=request.connector,
            action=request.action,
            success=True,
            data={
                "before_state": {"changed": False},
                "after_state": {"changed": True},
            },
        )


class _PluginEntryPoint:
    def __init__(
        self,
        events: list[str],
        revocations: TestApprovalRevocations,
        *,
        name: str = "fixture_plugin",
    ) -> None:
        self.name = name
        self.events = events
        self.revocations = revocations
        self.load_calls = 0
        self.revocation_lookups_at_load: list[int] = []

    def load(self):  # type: ignore[no-untyped-def]
        self.load_calls += 1
        self.events.append("factory_load")
        self.revocation_lookups_at_load.append(self.revocations.lookups)

        def factory(**_kwargs: object) -> _PluginRuntime:
            self.events.append("factory_construct")
            return _PluginRuntime(self.events)

        return factory


class _RecordingAuthority(TestGovernedAttemptAuthority):
    def __init__(
        self,
        *,
        revocations: TestApprovalRevocations,
        events: list[str],
        plugin_backend_controls: tuple[str, ...] = ("fixture_plugin",),
        plugin_egress_controls: tuple[str, ...] = ("no_network",),
    ) -> None:
        super().__init__(
            revocations=revocations,
            plugin_backend_controls=plugin_backend_controls,
            plugin_egress_controls=plugin_egress_controls,
        )
        self.events = events
        self.bundles = []

    def authorize_governed_attempt(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.events.append("authority")
        bundle = super().authorize_governed_attempt(*args, **kwargs)
        self.bundles.append(bundle)
        return bundle


def _plugin_profile_and_environment(
    tmp_path: Path,
    *,
    backend: str = "fixture_plugin",
    execution_posture: str = "fixture_only",
) -> tuple[Path, Path]:
    profile = tmp_path / "plugin-profile"
    shutil.copytree(FIXTURE_PROFILE, profile)
    connector_path = profile / "connectors" / "fixture_source.yaml"
    connector = yaml.safe_load(connector_path.read_text(encoding="utf-8"))["connector"]
    connector["required_capability_descriptors"] = [f"connector.plugin.{backend}"]
    connector["execution_postures"] = {
        "fixture_only": {
            "live_backend_posture": "fixture_only",
            "allowed_network_egress": "no_network",
        },
        "operator_wrapper": {
            "live_backend_posture": "live_backend",
            "allowed_network_egress": "local_subprocess",
        },
    }
    connector_path.write_text(
        yaml.safe_dump({"connector": connector}),
        encoding="utf-8",
    )

    environment = yaml.safe_load(FIXTURE_ENVIRONMENT.read_text(encoding="utf-8"))
    connector_config = {
        "enabled": True,
        "backend": backend,
        "execution_posture": execution_posture,
    }
    if execution_posture == "fixture_only":
        connector_config["fixture_only"] = True
    else:
        connector_config.update(
            {
                "fixture_only": False,
                "wrapper_command": ["operator-wrapper", "--bounded"],
            }
        )
    environment["environment"]["connectors"]["fixture_source"] = connector_config
    environment_path = tmp_path / "plugin-environment.yaml"
    environment_path.write_text(yaml.safe_dump(environment), encoding="utf-8")
    return profile / "profile.yaml", environment_path


def _instrument_store(store: FileStore, events: list[str], monkeypatch) -> None:  # type: ignore[no-untyped-def]
    claim_once = store.claim_governance_decision_once
    save_permit = store.save_execution_permit
    start_attempt = store.start_execution_attempt

    def record_claim(**binding):  # type: ignore[no-untyped-def]
        result = claim_once(**binding)
        events.append("claim")
        return result

    def record_permit(permit):  # type: ignore[no-untyped-def]
        events.append("permit")
        return save_permit(permit)

    def record_attempt(**binding):  # type: ignore[no-untyped-def]
        events.append("attempt")
        return start_attempt(**binding)

    monkeypatch.setattr(store, "claim_governance_decision_once", record_claim)
    monkeypatch.setattr(store, "save_execution_permit", record_permit)
    monkeypatch.setattr(store, "start_execution_attempt", record_attempt)


def _plan_plugin_operation(
    controller: OperationController,
    *,
    profile: Path,
    environment: Path,
):  # type: ignore[no-untyped-def]
    operation = controller.plan(
        profile_path=profile,
        environment_path=environment,
        intent="apply_fixture_change",
        target="fixture-target",
        mode="apply",
    )
    controller.approve(operation.id, approved_by="operator")
    return operation


def _assert_no_claim_or_factory(events: list[str]) -> None:
    assert "claim" not in events
    assert "permit" not in events
    assert "attempt" not in events
    assert "factory_load" not in events
    assert "factory_construct" not in events
    assert "io" not in events


def test_real_v02_plugin_authority_precedes_claim_permit_attempt_factory_and_io(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, environment = _plugin_profile_and_environment(tmp_path)
    store = FileStore(tmp_path / ".rexecop")
    events: list[str] = []
    _instrument_store(store, events, monkeypatch)
    revocations = TestApprovalRevocations()
    point = _PluginEntryPoint(events, revocations)
    authority = _RecordingAuthority(revocations=revocations, events=events)

    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        return_value=[point],
    ):
        controller = OperationController(
            store,
            **governed_runtime_kwargs(
                revocations=revocations,
                authority=authority,
            ),
        )
        operation = _plan_plugin_operation(
            controller,
            profile=profile,
            environment=environment,
        )
        result = controller.start(operation.id)

    assert result.state == "completed"
    assert events == [
        "authority",
        "claim",
        "permit",
        "attempt",
        "factory_load",
        "factory_construct",
        "io",
    ]
    assert point.load_calls == 1
    assert point.revocation_lookups_at_load == [3]
    assert len(authority.bundles) == 1
    admission = authority.bundles[0].governed_admission
    assert isinstance(admission, TypedExecutionGovernedAdmissionV02)
    assert admission.schema_version == "v0.2"
    assert admission.plugin_backend_class == "fixture_plugin"
    assert admission.plugin_egress_class == "no_network"
    assert admission.plugin_identity_class == "plugin_declared"
    permit = store.load_execution_permit(operation.id, "apply_change")
    assert permit["governed_admission_binding"]["composite_admission_digest"] == (
        admission.admission_digest
    )


def test_incompatible_v02_surface_fails_before_claim_permit_factory_or_io(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, environment = _plugin_profile_and_environment(tmp_path)
    store = FileStore(tmp_path / ".rexecop")
    events: list[str] = []
    _instrument_store(store, events, monkeypatch)
    revocations = TestApprovalRevocations()
    point = _PluginEntryPoint(events, revocations)
    authority = _RecordingAuthority(revocations=revocations, events=events)

    import govengine.typed_execution_governed_admission as governed_surface

    monkeypatch.setattr(
        governed_surface,
        "TYPED_EXECUTION_GOVERNED_ADMISSION_V02_SCHEMA_VERSION",
        "v0.3",
    )
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        return_value=[point],
    ):
        controller = OperationController(
            store,
            **governed_runtime_kwargs(
                revocations=revocations,
                authority=authority,
            ),
        )
        operation = _plan_plugin_operation(
            controller,
            profile=profile,
            environment=environment,
        )
        result = controller.start(operation.id)

    assert result.state == "failed"
    _assert_no_claim_or_factory(events)


def test_missing_v02_surface_fails_before_claim_permit_factory_or_io(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, environment = _plugin_profile_and_environment(tmp_path)
    store = FileStore(tmp_path / ".rexecop")
    events: list[str] = []
    _instrument_store(store, events, monkeypatch)
    revocations = TestApprovalRevocations()
    point = _PluginEntryPoint(events, revocations)

    import govengine.typed_execution_governed_admission as governed_surface

    class _SurfaceRemovingAuthority(_RecordingAuthority):
        def authorize_governed_attempt(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            bundle = super().authorize_governed_attempt(*args, **kwargs)
            monkeypatch.delattr(
                governed_surface,
                "validate_typed_execution_governed_admission_v02",
            )
            return bundle

    authority = _SurfaceRemovingAuthority(revocations=revocations, events=events)
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        return_value=[point],
    ):
        controller = OperationController(
            store,
            **governed_runtime_kwargs(
                revocations=revocations,
                authority=authority,
            ),
        )
        operation = _plan_plugin_operation(
            controller,
            profile=profile,
            environment=environment,
        )
        result = controller.start(operation.id)

    assert result.state == "failed"
    _assert_no_claim_or_factory(events)


def test_expired_v02_fails_before_claim_permit_factory_or_io(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, environment = _plugin_profile_and_environment(tmp_path)
    store = FileStore(tmp_path / ".rexecop")
    events: list[str] = []
    _instrument_store(store, events, monkeypatch)
    revocations = TestApprovalRevocations()
    point = _PluginEntryPoint(events, revocations)
    authority = _RecordingAuthority(revocations=revocations, events=events)
    expired_at = datetime.now(UTC) + timedelta(seconds=31)

    class _ExpiredClock:
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return expired_at if tz is not None else expired_at.replace(tzinfo=None)

    monkeypatch.setattr(runtime_authority_module, "datetime", _ExpiredClock)
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        return_value=[point],
    ):
        controller = OperationController(
            store,
            **governed_runtime_kwargs(
                revocations=revocations,
                authority=authority,
            ),
        )
        operation = _plan_plugin_operation(
            controller,
            profile=profile,
            environment=environment,
        )
        result = controller.start(operation.id)

    assert result.state == "failed"
    _assert_no_claim_or_factory(events)


@pytest.mark.parametrize(
    ("backend", "execution_posture"),
    [
        ("arbitrary_plugin", "fixture_only"),
        ("fixture_plugin", "operator_wrapper"),
    ],
)
def test_authority_owned_policy_controls_deny_mismatched_plugin_shape(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    execution_posture: str,
) -> None:
    profile, environment = _plugin_profile_and_environment(
        tmp_path,
        backend=backend,
        execution_posture=execution_posture,
    )
    store = FileStore(tmp_path / ".rexecop")
    events: list[str] = []
    _instrument_store(store, events, monkeypatch)
    revocations = TestApprovalRevocations()
    point = _PluginEntryPoint(events, revocations, name=backend)
    authority = _RecordingAuthority(revocations=revocations, events=events)

    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        return_value=[point],
    ):
        controller = OperationController(
            store,
            **governed_runtime_kwargs(
                revocations=revocations,
                authority=authority,
            ),
        )
        operation = _plan_plugin_operation(
            controller,
            profile=profile,
            environment=environment,
        )
        result = controller.start(operation.id)

    assert result.state == "failed"
    assert events == ["authority"]
    _assert_no_claim_or_factory(events)


def test_signed_decision_control_tamper_fails_before_atomic_claim_and_factory(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, environment = _plugin_profile_and_environment(tmp_path)
    store = FileStore(tmp_path / ".rexecop")
    events: list[str] = []
    _instrument_store(store, events, monkeypatch)
    revocations = TestApprovalRevocations()
    point = _PluginEntryPoint(events, revocations)

    class _SignedControlTamperingAuthority(_RecordingAuthority):
        def authorize_governed_attempt(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            bundle = super().authorize_governed_attempt(*args, **kwargs)
            decision = replace(
                bundle.decision,
                controls=replace(
                    bundle.decision.controls,
                    allowed_backend_classes=("arbitrary_plugin",),
                ),
                decision_digest="",
            )
            decision = replace(
                decision,
                decision_digest=_governance_decision_body_digest(decision),
            )
            return replace(
                bundle,
                decision=decision,
                signed_artifact=sign_governance_decision(
                    decision,
                    signer=DemoDigestSigner(signer_id="test-decision-signer"),
                    payload_ref=f"artifact://tests/{decision.decision_id}",
                ),
            )

    authority = _SignedControlTamperingAuthority(
        revocations=revocations,
        events=events,
    )
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        return_value=[point],
    ):
        controller = OperationController(
            store,
            **governed_runtime_kwargs(
                revocations=revocations,
                authority=authority,
            ),
        )
        operation = _plan_plugin_operation(
            controller,
            profile=profile,
            environment=environment,
        )
        result = controller.start(operation.id)

    assert result.state == "failed"
    _assert_no_claim_or_factory(events)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capability_requirements_digest", "sha256:" + "0" * 64),
        ("admitted_at", "2999-01-01T00:00:00Z"),
    ],
)
def test_v02_composite_tamper_fails_before_atomic_claim(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    profile, environment = _plugin_profile_and_environment(tmp_path)
    store = FileStore(tmp_path / ".rexecop")
    events: list[str] = []
    _instrument_store(store, events, monkeypatch)
    revocations = TestApprovalRevocations()
    point = _PluginEntryPoint(events, revocations)

    class _TamperingAuthority(_RecordingAuthority):
        def authorize_governed_attempt(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            bundle = super().authorize_governed_attempt(*args, **kwargs)
            return replace(
                bundle,
                governed_admission=replace(
                    bundle.governed_admission,
                    **{field: value},
                ),
            )

    authority = _TamperingAuthority(revocations=revocations, events=events)
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        return_value=[point],
    ):
        controller = OperationController(
            store,
            **governed_runtime_kwargs(
                revocations=revocations,
                authority=authority,
            ),
        )
        operation = _plan_plugin_operation(
            controller,
            profile=profile,
            environment=environment,
        )
        result = controller.start(operation.id)

    assert result.state == "failed"
    assert "claim" not in events
    assert "permit" not in events
    assert "factory_load" not in events
    assert "io" not in events


def test_v02_composite_tamper_at_pre_io_blocks_lazy_factory(
    tmp_path: Path,
    allow_lab_mutation_runtime_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, environment = _plugin_profile_and_environment(tmp_path)
    store = FileStore(tmp_path / ".rexecop")
    events: list[str] = []
    _instrument_store(store, events, monkeypatch)
    revocations = TestApprovalRevocations()
    point = _PluginEntryPoint(events, revocations)
    authority = _RecordingAuthority(revocations=revocations, events=events)

    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        return_value=[point],
    ):
        controller = OperationController(
            store,
            **governed_runtime_kwargs(
                revocations=revocations,
                authority=authority,
            ),
        )
        consumer = controller.orchestrator._governed_attempt_consumer
        assert consumer is not None
        require_current = consumer.require_current
        calls = 0

        def tamper_second_check(claim, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 2:
                claim = replace(
                    claim,
                    governed_admission=replace(
                        claim.governed_admission,
                        admitted_at="2999-01-01T00:00:00Z",
                    ),
                )
            return require_current(claim, **kwargs)

        monkeypatch.setattr(consumer, "require_current", tamper_second_check)
        operation = _plan_plugin_operation(
            controller,
            profile=profile,
            environment=environment,
        )
        result = controller.start(operation.id)

    assert result.state == "failed"
    assert events[:4] == ["authority", "claim", "permit", "attempt"]
    assert "factory_load" not in events
    assert "io" not in events
