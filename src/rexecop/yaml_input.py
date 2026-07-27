from __future__ import annotations

import copy
import math
import re
from pathlib import Path
from typing import Any

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, ScalarNode

from rexecop.errors import _InvalidJsonValue, _InvalidYamlStructure

DEFAULT_MAX_BYTES = 1024 * 1024
DEFAULT_MAX_DEPTH = 64
DEFAULT_MAX_NODES = 50_000

_ALLOWED_TAGS = frozenset(
    {
        "tag:yaml.org,2002:null",
        "tag:yaml.org,2002:bool",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:str",
        "tag:yaml.org,2002:seq",
        "tag:yaml.org,2002:map",
    }
)
_BOOL_PATTERN = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")


class _BoundedSafeLoader(yaml.SafeLoader):
    yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)

    def __init__(self, stream: str, *, max_depth: int, max_nodes: int) -> None:
        self._maximum_depth = max_depth
        self._maximum_nodes = max_nodes
        self._composition_depth = 0
        self._composed_nodes = 0
        super().__init__(stream)

    def compose_node(self, parent: Any, index: Any) -> yaml.Node:
        if self.check_event(AliasEvent):
            raise _InvalidYamlStructure()
        if self._composition_depth >= self._maximum_depth:
            raise _InvalidYamlStructure()
        self._composed_nodes += 1
        if self._composed_nodes > self._maximum_nodes:
            raise _InvalidYamlStructure()
        self._composition_depth += 1
        try:
            node = super().compose_node(parent, index)
        finally:
            self._composition_depth -= 1
        if node is None or node.tag not in _ALLOWED_TAGS:
            raise _InvalidYamlStructure()
        return node


for _first_character, _resolvers in tuple(
    _BoundedSafeLoader.yaml_implicit_resolvers.items()
):
    _BoundedSafeLoader.yaml_implicit_resolvers[_first_character] = [
        (tag, pattern)
        for tag, pattern in _resolvers
        if tag not in {"tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp"}
    ]
_BoundedSafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    _BOOL_PATTERN,
    list("tTfF"),
)


def _construct_string_key_mapping(
    loader: yaml.SafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        if not isinstance(key_node, ScalarNode) or key_node.tag != "tag:yaml.org,2002:str":
            raise _InvalidYamlStructure()
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key in mapping:
            raise _InvalidYamlStructure()
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_BoundedSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_string_key_mapping,
)


def load_yaml_file(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> Any:
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as exc:
        raise _InvalidYamlStructure() from exc
    return load_yaml_bytes(
        raw,
        max_bytes=max_bytes,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )


def load_yaml_text(
    text: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> Any:
    try:
        raw = text.encode("utf-8")
    except UnicodeError as exc:
        raise _InvalidYamlStructure() from exc
    return load_yaml_bytes(
        raw,
        max_bytes=max_bytes,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )


def load_yaml_bytes(
    raw: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> Any:
    if min(max_bytes, max_depth, max_nodes) < 1 or len(raw) > max_bytes:
        raise _InvalidYamlStructure()
    try:
        text = raw.decode("utf-8")
        loader = _BoundedSafeLoader(
            text,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        try:
            value = loader.get_single_data()
        finally:
            loader.dispose()
        _validate_json_value(value, yaml_input=True)
        return value
    except _InvalidYamlStructure:
        raise
    except (UnicodeError, yaml.YAMLError, RecursionError, TypeError, ValueError) as exc:
        raise _InvalidYamlStructure() from exc


def ensure_finite_json_value(value: Any) -> None:
    try:
        _validate_json_value(value, yaml_input=False)
    except RecursionError as exc:
        raise _InvalidJsonValue() from exc


def _validate_json_value(value: Any, *, yaml_input: bool) -> None:
    error = _InvalidYamlStructure if yaml_input else _InvalidJsonValue
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise error()
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, yaml_input=yaml_input)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise error()
            _validate_json_value(item, yaml_input=yaml_input)
        return
    raise error()
