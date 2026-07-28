from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rexecop.cli import app
from rexecop.examples.first_run import FIXTURE_FILES, FIXTURE_VERSION, materialize_first_run_demo

ROOT = Path(__file__).resolve().parents[1]
MIRROR = ROOT / "examples" / "first-run-demo"
runner = CliRunner()


def _file_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_packaged_first_run_fixture_exactly_matches_source_mirror(tmp_path: Path) -> None:
    materialized = materialize_first_run_demo(tmp_path / "first-run")
    output = Path(str(materialized["output"]))
    assert tuple(materialized["files"]) == FIXTURE_FILES
    assert materialized["fixture_version"] == FIXTURE_VERSION
    assert _file_bytes(output) == _file_bytes(MIRROR)


def test_materialize_new_directory_has_exact_bytes_and_no_staging_residue(tmp_path: Path) -> None:
    output = tmp_path / "first-run"

    result = materialize_first_run_demo(output)

    assert result["status"] == "materialized"
    assert result["example"] == "first-run-demo"
    assert result["output"] == str(output)
    assert list(result["files"]) == list(FIXTURE_FILES)
    assert _file_bytes(output) == _file_bytes(MIRROR)
    assert not list(tmp_path.glob(".rexecop-first-run-*"))


@pytest.mark.parametrize("kind", ("file", "nonempty_directory", "symlink_destination"))
def test_materialize_rejects_existing_output_without_traceback(tmp_path: Path, kind: str) -> None:
    output = tmp_path / "first-run"
    if kind == "file":
        output.write_text("operator data\n", encoding="utf-8")
        before = output.read_bytes()
    elif kind == "nonempty_directory":
        output.mkdir()
        (output / "operator.txt").write_text("operator data\n", encoding="utf-8")
        before = (output / "operator.txt").read_bytes()
    else:
        target = tmp_path / "operator-directory"
        target.mkdir()
        output.symlink_to(target, target_is_directory=True)
        before = _file_bytes(target)

    result = runner.invoke(app, ["examples", "materialize", "--output", str(output)])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    if kind == "file":
        assert output.read_bytes() == before
    elif kind == "nonempty_directory":
        assert (output / "operator.txt").read_bytes() == before
    else:
        assert _file_bytes(tmp_path / "operator-directory") == before
    assert not list(tmp_path.glob(".rexecop-first-run-*"))


def test_materialize_rejects_symlink_ancestor_and_repeat_without_adoption(tmp_path: Path) -> None:
    parent = tmp_path / "operator-parent"
    parent.mkdir()
    symlink_parent = tmp_path / "symlink-parent"
    symlink_parent.symlink_to(parent, target_is_directory=True)

    rejected = runner.invoke(
        app,
        ["examples", "materialize", "--output", str(symlink_parent / "first-run")],
    )
    assert rejected.exit_code == 1
    assert "Traceback" not in rejected.output
    assert not (parent / "first-run").exists()

    output = tmp_path / "first-run"
    assert runner.invoke(app, ["examples", "materialize", "--output", str(output)]).exit_code == 0
    repeated = runner.invoke(app, ["examples", "materialize", "--output", str(output)])
    assert repeated.exit_code == 1
    assert "Traceback" not in repeated.output
    assert _file_bytes(output) == _file_bytes(MIRROR)


def test_cli_materialize_returns_bounded_json(tmp_path: Path) -> None:
    output = tmp_path / "first-run"

    result = runner.invoke(app, ["examples", "materialize", "--output", str(output)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "status",
        "example",
        "fixture_version",
        "output",
        "files",
        "nonclaims",
    }
    assert payload["files"] == list(FIXTURE_FILES)
    assert len(payload["nonclaims"]) == 4
