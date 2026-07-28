from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rexecop.catalog.digest import canonical_digest
from rexecop.connectors.errors import READ_ONLY_MODES
from rexecop.connectors.fixture_loader import list_registered_connector_backends
from rexecop.connectors.http_support import destination_binding
from rexecop.connectors.registry import describe_connector_backend, list_builtin_connector_backends
from rexecop.errors import RExecOpValidationError
from rexecop.secrets.doctor import collect_secret_ref_bindings

BACKEND_CAPABILITY_DESCRIPTOR_SCHEMA = "rexecop.backend_capability_descriptor.v0.1"
BACKEND_CAPABILITY_SCHEMA_VERSION = "v0.1"

PLUGIN_EXECUTION_POSTURES: dict[str, tuple[str, str]] = {
    "fixture_only": ("fixture_only", "no_network"),
    "operator_wrapper": ("live_backend", "local_subprocess"),
}

_RAW_SHELL_BACKENDS = frozenset(
    {
        "shell",
        "local_shell",
        "ssh",
        "raw_shell",
        "subprocess",
    }
)


def assert_backend_is_declared(backend_class: str) -> None:
    backend = str(backend_class or "").strip()
    if not backend:
        raise RExecOpValidationError("backend capability missing")
    if backend in _RAW_SHELL_BACKENDS:
        raise RExecOpValidationError(f"raw shell backend blocked before IO: {backend}")
    if backend in list_builtin_connector_backends():
        return
    if backend in list_registered_connector_backends():
        return
    raise RExecOpValidationError(f"undeclared backend capability: {backend}")


def compile_connector_capability_descriptor(
    *,
    connector: str,
    backend_class: str,
    connector_config: Mapping[str, Any],
    mode: str,
    profile_execution_postures: Any = None,
) -> dict[str, Any]:
    """Compile digest-bound backend capability posture for one connector binding."""
    backend = str(backend_class or "").strip()
    assert_backend_is_declared(backend)
    class_descriptor = describe_connector_backend(backend)
    plugin_posture = _compile_plugin_execution_posture(
        class_descriptor.certification_tier,
        profile_execution_postures=profile_execution_postures,
        connector_config=connector_config,
    )
    secret_ref_requirements = _secret_ref_requirements(
        backend,
        connector,
        connector_config,
    )
    _validate_secret_ref_requirements(secret_ref_requirements)
    descriptor = {
        "schema": BACKEND_CAPABILITY_DESCRIPTOR_SCHEMA,
        "schema_version": BACKEND_CAPABILITY_SCHEMA_VERSION,
        "projection_kind": "runtime_projection",
        "connector": connector,
        "backend_class": backend,
        "identity_class": (
            "plugin_declared" if plugin_posture is not None else class_descriptor.identity_class
        ),
        "egress_class": (
            plugin_posture[1] if plugin_posture is not None else class_descriptor.egress_class
        ),
        "read_only_backend": class_descriptor.read_only_backend,
        "live_backend_posture": (
            plugin_posture[0]
            if plugin_posture is not None
            else _live_backend_posture(backend, connector_config)
        ),
        "network_boundary": (
            {
                "egress": plugin_posture[1],
                "host_declared": False,
            }
            if plugin_posture is not None
            else _network_boundary(backend, connector_config)
        ),
        "secret_ref_requirements": secret_ref_requirements,
        "declared_capability_descriptors": list(class_descriptor.capability_descriptors),
        "certification_tier": class_descriptor.certification_tier,
        "mode": mode,
        "non_claims": [
            "Runtime projection only; not a SCLite truth artifact.",
            "Does not resolve secret values or print connector endpoints.",
            "Does not prove GovEngine admission or host enforcement occurred.",
        ],
    }
    assert_backend_capability_allowed(descriptor, mode=mode)
    descriptor["digest"] = backend_capability_descriptor_digest(descriptor)
    return descriptor


def _compile_plugin_execution_posture(
    certification_tier: str,
    *,
    profile_execution_postures: Any,
    connector_config: Mapping[str, Any],
) -> tuple[str, str] | None:
    if certification_tier != "plugin":
        if profile_execution_postures is not None or connector_config.get(
            "execution_posture"
        ) is not None:
            raise RExecOpValidationError(
                "plugin execution posture is only valid for plugin backends"
            )
        return None
    if not isinstance(profile_execution_postures, Mapping):
        raise RExecOpValidationError(
            "plugin profile execution_postures declaration is required"
        )
    selected = str(connector_config.get("execution_posture") or "").strip()
    if not selected:
        raise RExecOpValidationError(
            "plugin environment execution_posture selection is required"
        )
    expected = PLUGIN_EXECUTION_POSTURES.get(selected)
    if expected is None:
        raise RExecOpValidationError(
            f"unsupported plugin execution posture: {selected}"
        )
    declaration = profile_execution_postures.get(selected)
    if not isinstance(declaration, Mapping):
        raise RExecOpValidationError(
            f"plugin execution posture is not declared by profile: {selected}"
        )
    unknown = sorted(
        str(key)
        for key in declaration
        if key not in {"live_backend_posture", "allowed_network_egress"}
    )
    if unknown:
        raise RExecOpValidationError(
            "plugin execution posture contains unsupported fields: " + ", ".join(unknown)
        )
    actual = (
        str(declaration.get("live_backend_posture") or "").strip(),
        str(declaration.get("allowed_network_egress") or "").strip(),
    )
    if actual != expected:
        raise RExecOpValidationError(
            f"plugin execution posture declaration contradicts {selected}"
        )
    fixture_only = connector_config.get("fixture_only")
    wrapper_command = connector_config.get("wrapper_command")
    wrapper_present = (
        bool(wrapper_command.strip())
        if isinstance(wrapper_command, str)
        else isinstance(wrapper_command, list)
        and bool(wrapper_command)
        and all(isinstance(item, str) and bool(item.strip()) for item in wrapper_command)
    )
    if selected == "fixture_only":
        if fixture_only is not True:
            raise RExecOpValidationError(
                "fixture_only plugin posture requires fixture_only true"
            )
        if "wrapper_command" in connector_config:
            raise RExecOpValidationError(
                "fixture_only plugin posture forbids wrapper_command"
            )
    elif fixture_only is not False or not wrapper_present:
        raise RExecOpValidationError(
            "operator_wrapper plugin posture requires fixture_only false and wrapper_command"
        )
    return expected


def backend_capability_descriptor_digest(descriptor: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in dict(descriptor).items()
        if key not in {"digest", "non_claims"}
    }
    return "sha256:" + canonical_digest(payload)


def assert_backend_capability_allowed(
    descriptor: Mapping[str, Any],
    *,
    mode: str,
) -> None:
    backend = str(descriptor.get("backend_class") or "").strip()
    if bool(descriptor.get("read_only_backend")) and mode not in READ_ONLY_MODES:
        raise RExecOpValidationError(
            f"readonly backend {backend} refuses mutating mode {mode}"
        )
    posture = str(descriptor.get("live_backend_posture") or "").strip()
    if posture == "fixture_only" and backend != "static_fixture":
        plugin_fixture = (
            str(descriptor.get("certification_tier") or "").strip() == "plugin"
        )
        if not plugin_fixture:
            raise RExecOpValidationError(
                f"fixture-only posture blocks live backend class {backend}"
            )
    if posture == "mock" and backend not in {"mock", "static_fixture"}:
        raise RExecOpValidationError(f"mock posture blocks undeclared live backend {backend}")


def _live_backend_posture(backend: str, connector_config: Mapping[str, Any]) -> str:
    if backend == "static_fixture":
        return "fixture_only"
    if backend == "mock":
        return "mock"
    if bool(connector_config.get("fixture_only")):
        return "fixture_only"
    return "live_backend"


def _network_boundary(backend: str, connector_config: Mapping[str, Any]) -> dict[str, Any]:
    if backend == "http_api":
        tls = connector_config.get("tls")
        boundary: dict[str, Any] = {
            "egress": "outbound_http",
            "tls_configured": isinstance(tls, Mapping) and bool(tls),
            "endpoint_declared": bool(
                connector_config.get("base_url") or connector_config.get("base_url_secret_ref")
            ),
        }
        base_url = str(connector_config.get("base_url") or "").strip()
        declared_binding = connector_config.get("destination_binding")
        if base_url:
            boundary["destination_binding"] = destination_binding(base_url)
        elif isinstance(declared_binding, Mapping):
            boundary["destination_binding"] = dict(declared_binding)
        return boundary
    if backend == "ssh_readonly":
        return {
            "egress": "outbound_ssh",
            "host_declared": bool(str(connector_config.get("host") or "").strip()),
            "port": int(connector_config.get("port") or 22),
            "deployment_posture": str(connector_config.get("deployment_posture") or "stable"),
            "known_hosts_policy": str(connector_config.get("known_hosts_policy") or "strict"),
        }
    if backend == "local_shell_readonly":
        return {
            "egress": "local_subprocess",
            "host_declared": False,
        }
    if backend == "static_fixture":
        return {"egress": "no_network", "host_declared": False}
    if backend == "mock":
        return {"egress": "no_network", "host_declared": False}
    return {"egress": "plugin_undeclared", "host_declared": False}


def _secret_ref_requirements(
    backend: str,
    connector: str,
    connector_config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    if backend == "http_api":
        if connector_config.get("base_url"):
            requirements.append(
                _requirement("base_url", required=False, present=True, kind="inline_endpoint")
            )
        else:
            present = bool(str(connector_config.get("base_url_secret_ref") or "").strip())
            requirements.append(
                _requirement(
                    "base_url_secret_ref",
                    required=True,
                    present=present,
                    kind="secret_ref",
                )
            )
        auth = connector_config.get("auth")
        if isinstance(auth, Mapping):
            present = bool(str(auth.get("secret_ref") or "").strip())
            requirements.append(
                _requirement(
                    "auth.secret_ref",
                    required=False,
                    present=present,
                    kind="secret_ref",
                )
            )
        tls = connector_config.get("tls")
        if isinstance(tls, Mapping):
            present = bool(str(tls.get("ca_file_secret_ref") or "").strip())
            requirements.append(
                _requirement(
                    "tls.ca_file_secret_ref",
                    required=False,
                    present=present,
                    kind="secret_ref",
                )
            )
    elif backend == "ssh_readonly":
        present = bool(str(connector_config.get("identity_file_secret_ref") or "").strip())
        requirements.append(
            _requirement(
                "identity_file_secret_ref",
                required=True,
                present=present,
                kind="secret_ref",
            )
        )
    elif backend in {"local_shell_readonly", "static_fixture", "mock"}:
        pass
    else:
        for binding in collect_secret_ref_bindings({"connectors": {connector: connector_config}}):
            prefix = f"connectors.{connector}."
            path = str(binding.get("path") or "").removeprefix(prefix)
            if path:
                requirements.append(
                    _requirement(path, required=False, present=True, kind="secret_ref")
                )
    return requirements


def _requirement(
    path: str,
    *,
    required: bool,
    present: bool,
    kind: str,
) -> dict[str, Any]:
    return {
        "path": path,
        "required": required,
        "present": present,
        "kind": kind,
    }


def _validate_secret_ref_requirements(requirements: list[dict[str, Any]]) -> None:
    missing = [
        str(item["path"])
        for item in requirements
        if bool(item.get("required")) and not bool(item.get("present"))
    ]
    if missing:
        raise RExecOpValidationError(
            "backend capability missing required secret refs: " + ", ".join(sorted(missing))
        )
