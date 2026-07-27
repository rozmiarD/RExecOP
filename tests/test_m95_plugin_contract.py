from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from rexecop.connectors.fixture_loader import (
    list_registered_connector_backends,
    load_connector_backend_for_connector,
)
from rexecop.errors import RExecOpValidationError
from rexecop.execution.internal_registry import (
    internal_action_plugin_inventory,
    load_internal_handlers,
)
from rexecop.plugins.diagnostic_identity import (
    ProjectedDiagnosticIdentity,
    project_diagnostic_identity,
)
from rexecop.profile.extension_manifest import build_plugin_compatibility_report
from rexecop.profile.resolver import resolve_profile_path
from rexecop.runtime.doctor import _check_plugin_posture


class EntryPoint:
    def __init__(self, name: str, loaded, *, distribution: object | None = None) -> None:
        self.name = name
        self._loaded = loaded
        self.dist = distribution
        self.load_calls = 0

    def load(self):
        self.load_calls += 1
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


class _BrokenDistribution:
    @property
    def name(self) -> str:
        raise RuntimeError("metadata lookup unavailable")


class _HostileProfileResult:
    def __str__(self) -> str:
        raise RuntimeError("profile-result-conversion-payload-4a19")


class _UnreadableEntryPointName:
    @property
    def name(self) -> str:
        raise RuntimeError("entry-point-name-payload-913c")


class _UnreadableMapping(dict):
    def __iter__(self):
        raise RuntimeError("mapping-iteration-payload-661e")


def _entry_points_for(*points: EntryPoint):
    def collect(**_kwargs):
        return list(points)

    return collect


def test_projected_diagnostic_identity_is_exactly_three_field_immutable_value() -> None:
    identity = project_diagnostic_identity("ordinary_plugin", kind="entry")

    assert [field.name for field in fields(ProjectedDiagnosticIdentity)] == [
        "kind",
        "display",
        "full_digest",
    ]
    assert ProjectedDiagnosticIdentity.__slots__ == ("kind", "display", "full_digest")
    assert identity.display == "ordinary_plugin"
    assert len(identity.full_digest) == 71
    assert identity.full_digest.startswith("sha256:")
    with pytest.raises(FrozenInstanceError):
        identity.display = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("raw", "kind", "prefix", "limit"),
    [
        ("safe_" + ("N" * 120), "entry", "safe_", 96),
        ("/private/entry", "entry", "unknown~", 96),
        ("/private/action", "action", "action~", 96),
        ("Failure/private", "exception", "Exception~", 64),
    ],
)
def test_projected_diagnostic_identity_uses_frozen_grammars_and_limits(
    raw: str,
    kind: str,
    prefix: str,
    limit: int,
) -> None:
    identity = project_diagnostic_identity(raw, kind=kind)  # type: ignore[arg-type]

    assert identity.display.startswith(prefix)
    assert len(identity.display) <= limit
    assert identity.full_digest.startswith("sha256:")
    assert len(identity.full_digest) == 71


def test_projected_diagnostic_identity_bounds_conversion_failure_to_neutral() -> None:
    identity = project_diagnostic_identity(_HostileProfileResult(), kind="entry")

    assert identity.display.startswith("unknown~")
    assert "profile-result-conversion-payload-4a19" not in identity.display
    assert len(identity.full_digest) == 71


def test_connector_plugin_cannot_collide_with_builtin() -> None:
    point = EntryPoint("http_api", lambda **_kwargs: object())
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        side_effect=_entry_points_for(point),
    ):
        with pytest.raises(RExecOpValidationError, match="plugin_name_collision"):
            list_registered_connector_backends()


def test_factory_typeerror_is_not_retried_as_zero_argument() -> None:
    calls = 0

    def broken_factory(**_kwargs):
        nonlocal calls
        calls += 1
        raise TypeError("private plugin detail")

    point = EntryPoint("broken", broken_factory)
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        side_effect=_entry_points_for(point),
    ):
        with pytest.raises(TypeError):
            load_connector_backend_for_connector(
                "broken",
                connector_name="fixture",
                config={},
                profile_root=None,
                mutating_allowed=False,
            )
    assert calls == 1


def test_connector_factory_v1_signature_is_required() -> None:
    def legacy_factory(required_legacy_argument):
        return required_legacy_argument

    point = EntryPoint("legacy", legacy_factory)
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        side_effect=_entry_points_for(point),
    ):
        with pytest.raises(RExecOpValidationError, match="plugin_contract_invalid"):
            load_connector_backend_for_connector(
                "legacy",
                connector_name="fixture",
                config={},
                profile_root=None,
                mutating_allowed=False,
            )


def test_internal_action_cannot_replace_builtin() -> None:
    with pytest.raises(RExecOpValidationError, match="plugin_name_collision"):
        load_internal_handlers(extra={"record_execution_checkpoint": lambda _ctx: {}})


def test_compatibility_report_bounds_plugin_exception_text() -> None:
    secret = "private-plugin-exception-detail"

    def broken_factory(**_kwargs):
        raise RuntimeError(secret)

    point = EntryPoint("broken", broken_factory)
    with patch(
        "rexecop.connectors.fixture_loader.entry_points",
        side_effect=_entry_points_for(point),
    ):
        report = build_plugin_compatibility_report()

    assert report["status"] == "failed"
    assert report["security_posture"]["execution_model"] == "trusted_in_process"
    assert secret not in json.dumps(report)


def test_internal_inventory_redacts_entry_point_load_failure() -> None:
    sensitive = "diagnostic-payload-must-not-leak-91d3"
    sensitive_distribution = "/private/operator/secret-distribution"
    hostile_exception = type("HostileDiagnosticFailure", (RuntimeError,), {})
    hostile_exception.__name__ = "Failure/private/module.target"
    point = EntryPoint(
        "broken\nregistrar",
        hostile_exception(f"{sensitive} /tmp/operator-private.py"),
        distribution=SimpleNamespace(name=sensitive_distribution),
    )
    with patch(
        "rexecop.execution.internal_registry.entry_points",
        side_effect=_entry_points_for(point),
    ):
        inventory = internal_action_plugin_inventory()

    assert point.load_calls == 1
    assert len(inventory) == 1
    assert inventory[0]["name"].startswith("unknown~")
    assert inventory[0]["distribution"].startswith("unknown~")
    assert inventory[0]["status"] == "failed"
    assert inventory[0]["errors"] == ["entry_point_load_failed"]
    assert inventory[0]["registered_actions"] == []
    assert inventory[0]["exception_class"].startswith("Exception~")
    assert "identity_digest" not in inventory[0]
    assert "full_digest" not in inventory[0]
    assert sensitive not in json.dumps(inventory)
    assert "/tmp/operator-private.py" not in json.dumps(inventory)
    assert sensitive_distribution not in json.dumps(inventory)
    assert "Failure/private/module.target" not in json.dumps(inventory)


def test_internal_inventory_bounds_enumeration_failure() -> None:
    sensitive = "enumeration-payload-must-not-leak-28a1"
    with patch(
        "rexecop.execution.internal_registry.entry_points",
        side_effect=RuntimeError(sensitive),
    ):
        inventory = internal_action_plugin_inventory()

    assert inventory[0]["status"] == "failed"
    assert inventory[0]["errors"] == ["entry_point_enumeration_failed"]
    assert inventory[0]["exception_class"] == "RuntimeError"
    assert sensitive not in json.dumps(inventory)


@pytest.mark.parametrize(
    ("loaded", "reason_code"),
    [
        (object(), "registrar_not_callable"),
        (lambda _required: {}, "registrar_contract_invalid"),
        (
            lambda: (_ for _ in ()).throw(RuntimeError("registrar-private-payload")),
            "registrar_call_failed",
        ),
        (lambda: ["not", "a", "mapping"], "registrar_returned_non_mapping"),
        (lambda: _UnreadableMapping(), "registrar_mapping_unreadable"),
        (
            lambda: {"record_execution_checkpoint": lambda _context: {}},
            "action_collision",
        ),
    ],
)
def test_internal_inventory_uses_stable_failure_codes(loaded, reason_code: str) -> None:
    point = EntryPoint("contract_probe", loaded)
    with patch(
        "rexecop.execution.internal_registry.entry_points",
        side_effect=_entry_points_for(point),
    ):
        inventory = internal_action_plugin_inventory()

    assert point.load_calls == 1
    assert inventory[0]["status"] == "failed"
    assert inventory[0]["errors"] == [reason_code]


def test_successful_internal_inventory_preserves_exact_legacy_shape_and_order() -> None:
    point = EntryPoint("healthy_actions", lambda: {"healthy_action": lambda _context: {}})
    with patch(
        "rexecop.execution.internal_registry.entry_points",
        side_effect=_entry_points_for(point),
    ):
        inventory = internal_action_plugin_inventory()

    assert inventory == [
        {
            "name": "healthy_actions",
            "entry_group": "rexecop.internal_actions",
            "trusted_in_process": True,
            "contract": "rexecop.internal_action_registrar.v1",
        }
    ]


def test_internal_inventory_bounds_identity_exception_action_and_cardinality() -> None:
    long_exception = type("DiagnosticFailure", (RuntimeError,), {})
    long_exception.__name__ = "Failure\n" + ("X" * 100)
    failing = EntryPoint("entry\n" + ("N" * 120), long_exception("hidden"))
    oversized = EntryPoint(
        "oversized",
        lambda: {f"action-{index}-" + ("A" * 120): (lambda _context: {}) for index in range(65)},
    )
    calls: dict[int, int] = {}

    def registrar(index: int):
        def register():
            calls[index] = calls.get(index, 0) + 1
            return {f"bounded_action_{index}": lambda _context: {}}

        return register

    many = [EntryPoint(f"point_{index}", registrar(index)) for index in range(70)]
    with patch(
        "rexecop.execution.internal_registry.entry_points",
        side_effect=_entry_points_for(failing, oversized, *many),
    ):
        inventory = internal_action_plugin_inventory()

    assert len(inventory) == 64
    assert len(inventory[0]["name"]) <= 96
    assert "\n" not in inventory[0]["name"]
    assert len(inventory[0]["exception_class"]) <= 64
    assert "\n" not in inventory[0]["exception_class"]
    assert len(inventory[1]["registered_actions"]) == 64
    assert all(len(name) <= 96 for name in inventory[1]["registered_actions"])
    assert inventory[-1]["errors"] == ["entry_point_inventory_limit_exceeded"]
    assert calls == {index: 1 for index in range(70)}


def test_distinct_long_raw_action_names_do_not_collide_and_have_distinct_displays() -> None:
    common = "action_" + ("A" * 120)
    first = common + "_first"
    second = common + "_second"
    hostile_action = "/private/operator/unsafe-action"
    points = (
        EntryPoint("first_registrar", lambda: {first: lambda _context: {}}),
        EntryPoint("second_registrar", lambda: {second: lambda _context: {}}),
        EntryPoint("hostile_registrar", lambda: {hostile_action: lambda _context: {}}),
    )
    with (
        patch(
            "rexecop.execution.internal_registry.entry_points",
            side_effect=_entry_points_for(*points),
        ),
        patch("rexecop.connectors.fixture_loader.entry_points", return_value=[]),
    ):
        report = build_plugin_compatibility_report()

    assert report["status"] == "passed"
    displays = [
        item["name"]
        for item in report["internal_actions"]
        if item["name"]
        not in {"record_execution_checkpoint", "record_rollback_marker"}
    ]
    assert displays[0] != displays[1]
    assert all(len(display) <= 96 for display in displays)
    assert displays[2].startswith("action~")
    assert hostile_action not in json.dumps(report)


def test_successful_internal_actions_remain_globally_sorted() -> None:
    point = EntryPoint(
        "sorted_actions",
        lambda: {
            "z_plugin_action": lambda _context: {},
            "a_plugin_action": lambda _context: {},
        },
    )
    with (
        patch(
            "rexecop.execution.internal_registry.entry_points",
            side_effect=_entry_points_for(point),
        ),
        patch("rexecop.connectors.fixture_loader.entry_points", return_value=[]),
    ):
        report = build_plugin_compatibility_report()

    names = [item["name"] for item in report["internal_actions"]]
    assert names == sorted(names)


def test_long_valid_connector_name_is_probed_by_raw_identity() -> None:
    name = "connector_" + ("C" * 140)

    def factory(**_kwargs):
        return SimpleNamespace(invoke=lambda _request: {})

    point = EntryPoint(name, factory)
    with (
        patch(
            "rexecop.connectors.fixture_loader.entry_points",
            side_effect=_entry_points_for(point),
        ),
        patch(
            "rexecop.execution.internal_registry.entry_points",
            return_value=[],
        ),
    ):
        report = build_plugin_compatibility_report()

    assert report["status"] == "passed"
    assert report["connector_backends"][0]["status"] == "passed"
    assert report["connector_backends"][0]["name"] != name
    assert len(report["connector_backends"][0]["name"]) <= 96
    assert report["connector_backends"][0]["name"] == report["inventory"][
        "connector_backends"
    ][0]["name"]
    assert list(report["connector_backends"][0]) == [
        "name",
        "kind",
        "entry_group",
        "status",
        "errors",
        "contract",
        "trusted_in_process",
    ]
    assert list(report["inventory"]["connector_backends"][0]) == [
        "name",
        "entry_group",
        "trusted_in_process",
        "contract",
        "name_collision",
    ]
    assert "identity_digest" not in json.dumps(report)
    assert "full_digest" not in json.dumps(report)
    assert point.load_calls == 1


def test_distinct_connector_failures_remain_distinct_without_public_digest_tokens() -> None:
    first = EntryPoint("/private/connector-one", object())
    second = EntryPoint("/private/connector-two", object())
    with (
        patch(
            "rexecop.connectors.fixture_loader.entry_points",
            side_effect=_entry_points_for(first, second),
        ),
        patch("rexecop.execution.internal_registry.entry_points", return_value=[]),
    ):
        report = build_plugin_compatibility_report()

    failures = report["incompatible_plugins"]
    assert len(failures) == 2
    assert failures[0]["name"] != failures[1]["name"]
    assert all(item["name"].startswith("unknown~") for item in failures)
    assert "identity_digest" not in json.dumps(report)
    assert "full_digest" not in json.dumps(report)


def test_exact_raw_long_internal_entry_point_allowlist_is_allowed() -> None:
    name = "internal_" + ("I" * 140)
    point = EntryPoint(name, lambda: {"allowed_action": lambda _context: {}})
    with (
        patch(
            "rexecop.execution.internal_registry.entry_points",
            side_effect=_entry_points_for(point),
        ),
        patch("rexecop.connectors.fixture_loader.entry_points", return_value=[]),
    ):
        checked = _check_plugin_posture("stable", name)

    assert checked["status"] == "passed"
    assert checked["details"]["installed"] == checked["details"]["allowlist"]
    assert all(len(identity) <= 96 for identity in checked["details"]["installed"])
    assert "sha256:" not in json.dumps(checked)


def test_representative_long_identity_and_exception_outputs_remain_bounded() -> None:
    huge_name = "entry\n" + ("N" * 4096)
    huge_exception = type("HugeDiagnosticFailure", (RuntimeError,), {})
    huge_exception.__name__ = "Failure\r" + ("X" * 4096)
    point = EntryPoint(huge_name, huge_exception("not reported"))
    with patch(
        "rexecop.execution.internal_registry.entry_points",
        side_effect=_entry_points_for(point),
    ):
        inventory = internal_action_plugin_inventory()

    assert len(inventory[0]["name"]) <= 96
    assert len(inventory[0]["exception_class"]) <= 64
    assert "\n" not in inventory[0]["name"]
    assert "\r" not in inventory[0]["exception_class"]


def test_metadata_lookup_failure_is_unknown_not_incompatible() -> None:
    point = EntryPoint(
        "healthy_actions",
        lambda: {"healthy_action": lambda _context: {}},
        distribution=_BrokenDistribution(),
    )
    with (
        patch(
            "rexecop.execution.internal_registry.entry_points",
            side_effect=_entry_points_for(point),
        ),
        patch("rexecop.connectors.fixture_loader.entry_points", return_value=[]),
    ):
        report = build_plugin_compatibility_report()

    registrar = report["inventory"]["internal_action_registrars"][0]
    assert list(registrar) == [
        "name",
        "entry_group",
        "trusted_in_process",
        "contract",
    ]
    assert report["incompatible_plugins"] == []
    assert report["status"] == "passed"
    assert "healthy_action" in {item["name"] for item in report["internal_actions"]}
    assert list(report) == [
        "schema",
        "status",
        "connector_backends",
        "internal_actions",
        "inventory",
        "failed",
        "incompatible_plugins",
        "security_posture",
    ]
    assert all(
        list(item)
        == [
            "name",
            "kind",
            "entry_group",
            "status",
            "errors",
            "contract",
            "trusted_in_process",
        ]
        for item in report["internal_actions"]
    )


def test_compatibility_report_snapshots_connector_inventory_once() -> None:
    with (
        patch(
            "rexecop.profile.extension_manifest.connector_backend_plugin_inventory",
            return_value=[],
        ) as connector_inventory,
        patch(
            "rexecop.execution.internal_registry.entry_points",
            return_value=[],
        ),
    ):
        report = build_plugin_compatibility_report()

    connector_inventory.assert_called_once_with()
    assert report["inventory"]["connector_backends"] == []


def test_compatibility_report_bounds_connector_enumeration_failure() -> None:
    sensitive = "connector-enumeration-payload-7e2a"
    with (
        patch(
            "rexecop.profile.extension_manifest.connector_backend_plugin_inventory",
            side_effect=RuntimeError(sensitive),
        ),
        patch(
            "rexecop.execution.internal_registry.entry_points",
            return_value=[],
        ),
    ):
        report = build_plugin_compatibility_report()

    assert report["status"] == "failed"
    assert report["incompatible_plugins"][0]["reason_codes"] == ["entry_point_enumeration_failed"]
    assert sensitive not in json.dumps(report)


def test_profile_resolver_redacts_entry_point_failure_without_chained_cause() -> None:
    sensitive = "profile-loader-payload-6ca4"
    name = "stale\nprofile" + ("P" * 120)
    point = EntryPoint(name, RuntimeError(f"{sensitive} /private/profile.py"))
    with patch(
        "rexecop.profile.resolver.entry_points",
        side_effect=_entry_points_for(point),
    ):
        with pytest.raises(RExecOpValidationError) as raised:
            resolve_profile_path(name)

    message = str(raised.value)
    assert sensitive not in message
    assert "/private/profile.py" not in message
    assert "RuntimeError" in message
    assert "\n" not in message
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_profile_resolver_redacts_enumeration_failure_without_chained_cause() -> None:
    sensitive = "profile-enumeration-payload-132b"
    with patch(
        "rexecop.profile.resolver.entry_points",
        side_effect=RuntimeError(sensitive),
    ):
        with pytest.raises(RExecOpValidationError) as raised:
            resolve_profile_path("stale_profile")

    message = str(raised.value)
    assert sensitive not in message
    assert "entry_point_enumeration_failed:RuntimeError" in message
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_profile_resolver_redacts_entry_point_name_access_and_tries_later_duplicate(
    tmp_path,
) -> None:
    valid = EntryPoint("stale_profile", lambda: str(tmp_path))
    with patch(
        "rexecop.profile.resolver.entry_points",
        side_effect=lambda **_kwargs: [_UnreadableEntryPointName(), valid],
    ):
        assert resolve_profile_path("stale_profile") == tmp_path

    with patch(
        "rexecop.profile.resolver.entry_points",
        side_effect=lambda **_kwargs: [_UnreadableEntryPointName()],
    ):
        with pytest.raises(RExecOpValidationError) as raised:
            resolve_profile_path("stale_profile")

    assert "entry-point-name-payload-913c" not in str(raised.value)
    assert "entry_point_name_failed:RuntimeError" in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_profile_resolver_distinguishes_registrar_invocation_failure() -> None:
    sensitive = "profile-invocation-payload-5ad8"

    def broken_registrar():
        raise RuntimeError(sensitive)

    point = EntryPoint("stale_profile", broken_registrar)
    with patch(
        "rexecop.profile.resolver.entry_points",
        side_effect=_entry_points_for(point),
    ):
        with pytest.raises(RExecOpValidationError) as raised:
            resolve_profile_path("stale_profile")

    assert sensitive not in str(raised.value)
    assert "entry_point_invocation_failed:RuntimeError" in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_profile_resolver_redacts_non_directory_and_tries_later_duplicate(
    tmp_path,
) -> None:
    name = "duplicate_profile"
    sensitive_path = "/private/operator/secret-profile-root"
    invalid = EntryPoint(name, lambda: sensitive_path)
    valid = EntryPoint(name, lambda: str(tmp_path))
    with patch(
        "rexecop.profile.resolver.entry_points",
        side_effect=_entry_points_for(invalid, valid),
    ):
        assert resolve_profile_path(name) == tmp_path

    with patch(
        "rexecop.profile.resolver.entry_points",
        side_effect=_entry_points_for(invalid),
    ):
        with pytest.raises(RExecOpValidationError) as raised:
            resolve_profile_path(name)

    message = str(raised.value)
    assert sensitive_path not in message
    assert "profile_path_not_directory:NotADirectoryError" in message
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_profile_resolver_redacts_loaded_result_conversion_failure() -> None:
    point = EntryPoint("hostile_profile", lambda: _HostileProfileResult())
    with patch(
        "rexecop.profile.resolver.entry_points",
        side_effect=_entry_points_for(point),
    ):
        with pytest.raises(RExecOpValidationError) as raised:
            resolve_profile_path("hostile_profile")

    message = str(raised.value)
    assert "profile-result-conversion-payload-4a19" not in message
    assert "profile_path_conversion_failed:RuntimeError" in message
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_profile_resolver_redacts_path_resolution_failure() -> None:
    sensitive = "path-resolution-payload-2f31"
    point = EntryPoint("resolution_profile", lambda: "relative-profile-root")
    with (
        patch(
            "rexecop.profile.resolver.entry_points",
            side_effect=_entry_points_for(point),
        ),
        patch("rexecop.profile.resolver.Path.resolve", side_effect=RuntimeError(sensitive)),
    ):
        with pytest.raises(RExecOpValidationError) as raised:
            resolve_profile_path("resolution_profile")

    message = str(raised.value)
    assert sensitive not in message
    assert "profile_path_resolution_failed:RuntimeError" in message
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_profile_resolver_redacts_path_validation_failure() -> None:
    sensitive = "path-validation-payload-e9bc"
    point = EntryPoint("validation_profile", lambda: "relative-profile-root")
    with (
        patch(
            "rexecop.profile.resolver.entry_points",
            side_effect=_entry_points_for(point),
        ),
        patch("rexecop.profile.resolver.Path.is_dir", side_effect=RuntimeError(sensitive)),
    ):
        with pytest.raises(RExecOpValidationError) as raised:
            resolve_profile_path("validation_profile")

    assert sensitive not in str(raised.value)
    assert "profile_path_validation_failed:RuntimeError" in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_strict_internal_loader_still_raises_entry_point_load_failure() -> None:
    point = EntryPoint("strict_probe", RuntimeError("strict-loader-failure"))
    with patch(
        "rexecop.execution.internal_registry.entry_points",
        side_effect=_entry_points_for(point),
    ):
        with pytest.raises(RuntimeError, match="strict-loader-failure"):
            load_internal_handlers()


def test_stable_doctor_requires_explicit_plugin_allowlist() -> None:
    connector = EntryPoint(
        "reviewed_connector",
        lambda **_kwargs: SimpleNamespace(invoke=lambda _request: {}),
    )
    internal = EntryPoint(
        "reviewed_actions",
        lambda: {"reviewed_action": lambda _context: {}},
    )
    with (
        patch(
            "rexecop.connectors.fixture_loader.entry_points",
            side_effect=_entry_points_for(connector),
        ),
        patch(
            "rexecop.execution.internal_registry.entry_points",
            side_effect=_entry_points_for(internal),
        ),
    ):
        blocked = _check_plugin_posture("stable", None)
        passed = _check_plugin_posture("stable", "reviewed_connector,reviewed_actions")

    assert blocked["status"] == "blocker"
    assert "reason_code" not in blocked["details"]
    assert set(blocked["details"]) == {
        "deployment_posture",
        "execution_model",
        "installed",
        "allowlist",
        "unallowed",
        "compatibility_failures",
    }
    assert blocked["next_action"] == (
        "set REXECOP_PLUGIN_ALLOWLIST to reviewed plugin entry-point names"
    )
    assert passed["status"] == "passed"
    assert passed["details"]["execution_model"] == "trusted_in_process"
    assert passed["details"]["sandboxed"] is False
    assert "sha256:" not in json.dumps(blocked)
    assert "sha256:" not in json.dumps(passed)


def test_doctor_marks_structured_incompatible_plugins_with_stable_reason() -> None:
    broken = EntryPoint("broken_actions", RuntimeError("private-incompatible-payload"))
    with (
        patch("rexecop.connectors.fixture_loader.entry_points", return_value=[]),
        patch(
            "rexecop.execution.internal_registry.entry_points",
            side_effect=_entry_points_for(broken),
        ),
    ):
        blocked = _check_plugin_posture("alpha", None)

    assert blocked["status"] == "blocker"
    assert blocked["details"]["reason_code"] == "plugin_incompatible"
    assert blocked["details"]["incompatible_plugins"][0]["reason_codes"] == [
        "entry_point_load_failed"
    ]
    assert "fresh environment" in blocked["next_action"]
    assert "repair or remove" in blocked["next_action"]
    assert "python -m pip check" in blocked["next_action"]
    assert blocked["next_action"].index("repair or remove") < blocked["next_action"].index(
        "python -m pip check"
    )
    assert blocked["next_action"].index("python -m pip check") < blocked[
        "next_action"
    ].index("rerun rexecop doctor")
    assert "do not execute" in blocked["next_action"]
