from __future__ import annotations

from copy import deepcopy

import pytest

from rexecop.errors import RExecOpValidationError
from rexecop.reaction.automation_graph import verify_runtime_automation_graph


def _chain() -> dict[str, object]:
    return {
        "source_operation_id": "op-1",
        "nodes": [
            {"node_id": "op", "node_type": "operation", "operation_id": "op-1", "depth": 0},
            {"node_id": "obs", "node_type": "observation", "depth": 0},
            {"node_id": "finding", "node_type": "finding", "depth": 0},
            {"node_id": "plan", "node_type": "reaction_plan", "depth": 0},
            {"node_id": "child", "node_type": "child_operation", "depth": 1},
        ],
        "edges": [
            {
                "edge_id": "e1",
                "edge_type": "observed",
                "from_node": "op",
                "to_node": "obs",
                "depth": 0,
            },
            {
                "edge_id": "e2",
                "edge_type": "detected",
                "from_node": "obs",
                "to_node": "finding",
                "depth": 0,
            },
            {
                "edge_id": "e3",
                "edge_type": "planned_reaction",
                "from_node": "finding",
                "to_node": "plan",
                "depth": 0,
            },
            {
                "edge_id": "e4",
                "edge_type": "admitted_child",
                "from_node": "plan",
                "to_node": "child",
                "depth": 1,
                "idempotency_key": "child-key",
            },
        ],
    }


def test_runtime_graph_reports_only_owned_checks() -> None:
    result = verify_runtime_automation_graph(_chain())

    assert result["status"] == "passed"
    assert "graph_acyclicity" in result["checked"]
    assert "admission_authenticity" in result["not_checked"]
    assert result["requires_external_verification"]["admission_authenticity"] == "govengine"


@pytest.mark.parametrize("mutation", ["cycle", "self_loop", "orphan", "fake_depth", "transition"])
def test_runtime_graph_rejects_invalid_semantics(mutation: str) -> None:
    chain = deepcopy(_chain())
    nodes = chain["nodes"]
    edges = chain["edges"]
    assert isinstance(nodes, list) and isinstance(edges, list)
    if mutation == "cycle":
        edges.append(
            {
                "edge_id": "bad",
                "edge_type": "observed",
                "from_node": "child",
                "to_node": "op",
                "depth": 1,
            }
        )
    elif mutation == "self_loop":
        edges.append(
            {
                "edge_id": "bad",
                "edge_type": "detected",
                "from_node": "obs",
                "to_node": "obs",
                "depth": 0,
            }
        )
    elif mutation == "orphan":
        nodes.append({"node_id": "orphan", "node_type": "finding", "depth": 0})
    elif mutation == "fake_depth":
        nodes[-1]["depth"] = 0
    else:
        edges[1]["edge_type"] = "planned_reaction"

    with pytest.raises(RExecOpValidationError):
        verify_runtime_automation_graph(chain)


def test_runtime_graph_rejects_duplicate_child_idempotency_across_branches() -> None:
    chain = deepcopy(_chain())
    nodes = chain["nodes"]
    edges = chain["edges"]
    assert isinstance(nodes, list) and isinstance(edges, list)
    nodes.append({"node_id": "child-2", "node_type": "child_operation", "depth": 1})
    edges.append(
        {
            "edge_id": "e5",
            "edge_type": "admitted_child",
            "from_node": "plan",
            "to_node": "child-2",
            "depth": 1,
            "idempotency_key": "child-key",
        }
    )

    with pytest.raises(RExecOpValidationError, match="idempotency"):
        verify_runtime_automation_graph(chain)
