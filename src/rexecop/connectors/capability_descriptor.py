from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rexecop.catalog.digest import canonical_digest
from rexecop.connectors.errors import READ_ONLY_MODES
from rexecop.connectors.fixture_loader import list_registered_connector_backends
from rexecop.connectors.registry import describe_connector_backend, list_builtin_connector_backends
from rexecop.errors import RExecOpValidationError
from rexecop.secrets.doctor import collect_secret_ref_bindings

BACKEND_CAPABILITY_DESCRIPTOR_SCHEMA = "rexecop.backend_capability_descriptor.v0.1"
BACKEND_CAPABILITY_SCHEMA_VERSION = "v0.1"

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
) -> dict[str, Any]:
    """Compile digest-bound backend capability posture for one connector binding."""
    backend = str(backend_class or "").strip()
    assert_backend_is_declared(backend)
    class_descriptor = describe_connector_backend(backend)
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
        "identity_class": class_descriptor.identity_class,
        "egress_class": class_descriptor.egress_class,
        "read_only_backend": class_descriptor.read_only_backend,
        "live_backend_posture": _live_backend_posture(backend, connector_config),
        "network_boundary": _network_boundary(backend, connector_config),
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
        return {
            "egress": "outbound_http",
            "tls_configured": isinstance(tls, Mapping) and bool(tls),
            "endpoint_declared": bool(
                connector_config.get("base_url") or connector_config.get("base_url_secret_ref")
            ),
        }
    if backend == "ssh_readonly":
        return {
            "egress": "outbound_ssh",
            "host_declared": bool(str(connector_config.get("host") or "").strip()),
            "port": int(connector_config.get("port") or 22),
            "known_hosts_policy": str(connector_config.get("known_hosts_policy") or "accept-new"),
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