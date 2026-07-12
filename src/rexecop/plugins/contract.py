from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from rexecop.errors import RExecOpValidationError

CONNECTOR_FACTORY_CONTRACT = "rexecop.connector_backend_factory.v1"
INTERNAL_REGISTRAR_CONTRACT = "rexecop.internal_action_registrar.v1"
CONNECTOR_FACTORY_ARGUMENTS = frozenset(
    {
        "connector_name",
        "config",
        "profile_root",
        "mutating_allowed",
        "secret_resolver",
    }
)


def validate_connector_factory(factory: Callable[..., Any]) -> None:
    signature = inspect.signature(factory)
    parameters = signature.parameters
    accepts_kwargs = any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values())
    missing = CONNECTOR_FACTORY_ARGUMENTS - set(parameters)
    if missing and not accepts_kwargs:
        raise RExecOpValidationError(
            "plugin_contract_invalid: connector factory must implement v1 keyword arguments"
        )
    unexpected_required = [
        name
        for name, item in parameters.items()
        if name not in CONNECTOR_FACTORY_ARGUMENTS
        and item.kind not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
        and item.default is inspect.Parameter.empty
    ]
    if unexpected_required:
        raise RExecOpValidationError(
            "plugin_contract_invalid: connector factory has unsupported required arguments"
        )


def validate_internal_registrar(registrar: Callable[..., Any]) -> None:
    signature = inspect.signature(registrar)
    required = [
        item
        for item in signature.parameters.values()
        if item.kind not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
        and item.default is inspect.Parameter.empty
    ]
    if required:
        raise RExecOpValidationError(
            "plugin_contract_invalid: internal action registrar must be zero-argument"
        )


def validate_runtime_invoke(runtime: Any) -> None:
    invoke = getattr(runtime, "invoke", None)
    if not callable(invoke):
        raise RExecOpValidationError(
            "plugin_contract_invalid: connector runtime requires invoke(request)"
        )
    signature = inspect.signature(invoke)
    positional = [
        item
        for item in signature.parameters.values()
        if item.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    if len(positional) != 1:
        raise RExecOpValidationError(
            "plugin_contract_invalid: connector runtime invoke must accept one request"
        )
