from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_workflow_security.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "rexecop_validate_workflow_security",
        SCRIPT,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_workflow_actions_are_pinned_to_reviewed_full_shas() -> None:
    report = _load_validator().validate_workflow_security()

    assert report["workflows"] == 3
    assert report["actions"] >= 20
    assert report["sibling_checkouts"] == 5


def _workflow_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    validator = _load_validator()
    workflows = tmp_path / "workflows"
    shutil.copytree(ROOT / ".github" / "workflows", workflows)
    monkeypatch.setattr(validator, "WORKFLOWS", workflows)
    return validator, workflows / "ci.yml"


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        ("", "workflow_ci_sibling_checkout_invalid:test"),
        ("main", "workflow_ci_sibling_ref_not_literal_sha:test:rozmiarD/tecrax"),
        ("v1.0.0", "workflow_ci_sibling_ref_not_literal_sha:test:rozmiarD/tecrax"),
        (
            "${{ github.sha }}",
            "workflow_ci_sibling_ref_not_literal_sha:test:rozmiarD/tecrax",
        ),
        (
            "ffffffffffffffffffffffffffffffffffffffff",
            "workflow_ci_sibling_checkout_mismatch",
        ),
    ],
)
def test_workflow_security_rejects_missing_moving_or_wrong_sibling_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
    expected: str,
) -> None:
    validator, ci_path = _workflow_fixture(tmp_path, monkeypatch)
    source = "ref: ae91fb278879fefc965dc3fd51d86889385dc4f0"
    changed = ci_path.read_text(encoding="utf-8").replace(
        source,
        f"ref: {replacement}",
        1,
    )
    ci_path.write_text(changed, encoding="utf-8")

    with pytest.raises(AssertionError, match=expected):
        validator.validate_workflow_security()


def test_workflow_security_rejects_duplicate_or_unexpected_sibling_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator, ci_path = _workflow_fixture(tmp_path, monkeypatch)
    text = ci_path.read_text(encoding="utf-8")
    checkout = """      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10
        with:
          repository: rozmiarD/tecrax
          path: ci-deps/tecrax
          ref: ae91fb278879fefc965dc3fd51d86889385dc4f0
          token: ${{ secrets.GITHUB_TOKEN }}
"""
    ci_path.write_text(text.replace(checkout, checkout * 2, 1), encoding="utf-8")

    with pytest.raises(AssertionError, match="workflow_ci_sibling_checkout_mismatch"):
        validator.validate_workflow_security()

    ci_path.write_text(
        text.replace("repository: rozmiarD/tecrax", "repository: rozmiarD/unexpected", 1),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="workflow_ci_sibling_checkout_mismatch"):
        validator.validate_workflow_security()


@pytest.mark.parametrize(
    "source",
    [
        'test "$GOVENGINE_REF" = "e65ad22ec25d74bbbb4969bd614981a8ed5e47c8"',
        'test "$SCLITE_REF" = "c065d7a157665351054bacc7b5e3ae12b7cc9d98"',
        'test "$PREVIOUS_VERSION" = "1.0.0rc1"',
    ],
)
def test_workflow_security_rejects_missing_exact_publish_ref_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    validator, _ = _workflow_fixture(tmp_path, monkeypatch)
    publish_path = validator.WORKFLOWS / "publish.yml"
    publish_path.write_text(
        publish_path.read_text(encoding="utf-8").replace(source, "", 1),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="workflow_publish_missing"):
        validator.validate_workflow_security()


@pytest.mark.parametrize(
    "name",
    ["govengine_ref", "sclite_ref", "version", "previous_version"],
)
def test_workflow_security_rejects_direct_shell_interpolation_of_publish_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    validator, _ = _workflow_fixture(tmp_path, monkeypatch)
    publish_path = validator.WORKFLOWS / "publish.yml"
    marker = "        run: |\n"
    publish_path.write_text(
        publish_path.read_text(encoding="utf-8").replace(
            marker,
            marker + f'          test "${{{{ inputs.{name} }}}}"\n',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="workflow_publish_direct_input_in_run"):
        validator.validate_workflow_security()
