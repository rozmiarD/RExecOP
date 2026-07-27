from __future__ import annotations

from pathlib import Path

import pytest

from rexecop.errors import RExecOpValidationError
from rexecop.yaml_input import load_yaml_bytes, load_yaml_file

ROOT = Path(__file__).resolve().parents[1]


def _assert_invalid(raw: bytes, **limits: int) -> None:
    with pytest.raises(RExecOpValidationError) as raised:
        load_yaml_bytes(raw, **limits)
    assert raised.value.reason_code == "invalid_yaml_structure"
    assert str(raised.value) == "YAML input is invalid or exceeds structural limits"


@pytest.mark.parametrize(
    "raw",
    [
        b"outer:\n  key: one\n  key: two\n",
        b"first: &shared [one]\nsecond: *shared\n",
        b"value: .nan\n",
        b"value: .inf\n",
        b"value: -.inf\n",
        b"1: value\n",
        b"first: one\n---\nsecond: two\n",
        b"value: !!timestamp 2026-07-27\n",
        b"value: !!binary ZGF0YQ==\n",
        b"value: !!python/object:builtins.object {}\n",
    ],
)
def test_bounded_yaml_rejects_ambiguous_or_non_json_structures(raw: bytes) -> None:
    _assert_invalid(raw)


def test_bounded_yaml_rejects_recursive_alias_without_recursion_error() -> None:
    _assert_invalid(b"value: &loop [*loop]\n")


def test_bounded_yaml_enforces_exact_byte_depth_and_node_limits() -> None:
    raw = b"a: one\n"
    assert load_yaml_bytes(raw, max_bytes=len(raw)) == {"a": "one"}
    _assert_invalid(raw, max_bytes=len(raw) - 1)

    assert load_yaml_bytes(b"a: [one]\n", max_depth=3) == {"a": ["one"]}
    _assert_invalid(b"a: [one]\n", max_depth=2)

    assert load_yaml_bytes(b"a: one\n", max_nodes=3) == {"a": "one"}
    _assert_invalid(b"a: one\n", max_nodes=2)


def test_yaml_resolver_preserves_strings_and_true_false_booleans() -> None:
    value = load_yaml_bytes(
        b"on: off\nyes_value: yes\nno_value: no\ndate: 2026-07-27\n"
        b"true_value: true\nfalse_value: FALSE\n"
    )

    assert value == {
        "on": "off",
        "yes_value": "yes",
        "no_value": "no",
        "date": "2026-07-27",
        "true_value": True,
        "false_value": False,
    }


def test_shipped_runtime_retry_on_key_remains_a_string() -> None:
    value = load_yaml_file(
        ROOT / "examples/environments/runtime-fixture.staging.example.yaml"
    )

    retry = value["environment"]["connectors"]["fixture_source"]["retry"]
    key = next(item for item in retry if item == "on")
    assert isinstance(key, str)
    assert retry[key] == ["timeout", "transient_connector_error"]


def test_file_reader_rejects_invalid_utf8_and_never_includes_path(tmp_path: Path) -> None:
    path = tmp_path / "private-marker.yaml"
    path.write_bytes(b"key: \xff\n")

    with pytest.raises(RExecOpValidationError) as raised:
        load_yaml_file(path)

    assert raised.value.reason_code == "invalid_yaml_structure"
    assert str(path) not in str(raised.value)
