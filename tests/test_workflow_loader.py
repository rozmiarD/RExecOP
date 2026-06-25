from __future__ import annotations

from pathlib import Path

from rexecop.workflow.loader import load_workflow

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    REPO_ROOT / "examples/profiles/runtime-fixture/workflows/inspect_fixture_state.yaml"
)


def test_workflow_yaml_loads() -> None:
    workflow = load_workflow(WORKFLOW)
    assert workflow.id == "runtime_fixture.inspect_fixture_state"
    assert len(workflow.steps) == 2
    assert workflow.required_connectors() == ["fixture_source"]
