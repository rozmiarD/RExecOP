from __future__ import annotations

from pathlib import Path

from rexecop.workflow.loader import load_workflow

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    REPO_ROOT / "examples/profiles/tecrax-fixture/workflows/check_backup_status.yaml"
)


def test_workflow_yaml_loads() -> None:
    workflow = load_workflow(WORKFLOW)
    assert workflow.id == "tecrax.check_backup_status"
    assert len(workflow.steps) == 5
    assert workflow.required_connectors() == ["proxmox", "pbs"]
