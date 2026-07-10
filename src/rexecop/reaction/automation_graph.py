from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from typing import Any

from rexecop.errors import RExecOpValidationError

_TRANSITIONS = {
    "observed": ("operation", "observation", 0),
    "detected": ("observation", "finding", 0),
    "planned_reaction": ("finding", "reaction_plan", 0),
    "admitted_child": ("reaction_plan", "child_operation", 1),
    "spawned_child": ("reaction_plan", "child_operation", 1),
    "emitted_receipt": ("child_operation", "execution_receipt", 0),
}


def verify_runtime_automation_graph(chain: Mapping[str, Any]) -> dict[str, Any]:
    """Verify graph semantics owned by RExecOp, without claiming external authority."""
    raw_nodes = chain.get("nodes")
    raw_edges = chain.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise RExecOpValidationError("automation graph requires node and edge lists")

    nodes: dict[str, Mapping[str, Any]] = {}
    for node in raw_nodes:
        if not isinstance(node, Mapping):
            raise RExecOpValidationError("automation graph node must be an object")
        node_id = str(node.get("node_id") or "")
        if not node_id or node_id in nodes:
            raise RExecOpValidationError("automation graph node ids must be unique")
        nodes[node_id] = node

    adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    indegree = {node_id: 0 for node_id in nodes}
    edge_ids: set[str] = set()
    child_keys: set[str] = set()
    for edge in raw_edges:
        if not isinstance(edge, Mapping):
            raise RExecOpValidationError("automation graph edge must be an object")
        edge_id = str(edge.get("edge_id") or "")
        if not edge_id or edge_id in edge_ids:
            raise RExecOpValidationError("automation graph edge ids must be unique")
        edge_ids.add(edge_id)
        source = str(edge.get("from_node") or "")
        target = str(edge.get("to_node") or "")
        if source not in nodes or target not in nodes:
            raise RExecOpValidationError("automation graph edge endpoint is missing")
        if source == target:
            raise RExecOpValidationError("automation graph self-loop is forbidden")

        edge_type = str(edge.get("edge_type") or "")
        transition = _TRANSITIONS.get(edge_type)
        if transition is None:
            raise RExecOpValidationError(f"unsupported automation transition: {edge_type}")
        source_type, target_type, depth_delta = transition
        if (
            nodes[source].get("node_type") != source_type
            or nodes[target].get("node_type") != target_type
        ):
            raise RExecOpValidationError(f"invalid node types for transition: {edge_type}")
        source_depth = _depth(nodes[source], "node")
        target_depth = _depth(nodes[target], "node")
        if target_depth != source_depth + depth_delta or _depth(edge, "edge") != target_depth:
            raise RExecOpValidationError(f"invalid depth for transition: {edge_type}")

        if edge_type in {"admitted_child", "spawned_child"}:
            key = str(edge.get("idempotency_key") or "")
            if not key or key in child_keys:
                raise RExecOpValidationError("child transition idempotency keys must be unique")
            child_keys.add(key)
        adjacency[source].append(target)
        indegree[target] += 1

    source_operation_id = str(chain.get("source_operation_id") or "")
    roots = [node_id for node_id, degree in indegree.items() if degree == 0]
    source_roots = [
        node_id
        for node_id in roots
        if nodes[node_id].get("node_type") == "operation"
        and str(nodes[node_id].get("operation_id") or "") == source_operation_id
    ]
    if len(roots) != 1 or len(source_roots) != 1:
        raise RExecOpValidationError("automation graph requires one source-operation root")

    queue = deque(roots)
    visited: set[str] = set()
    remaining = dict(indegree)
    while queue:
        current = queue.popleft()
        visited.add(current)
        for target in adjacency[current]:
            remaining[target] -= 1
            if remaining[target] == 0:
                queue.append(target)
    if len(visited) != len(nodes):
        raise RExecOpValidationError("automation graph contains a cycle or unreachable node")

    return {
        "status": "passed",
        "verification_posture": "rexecop_runtime_graph_v0.1",
        "checked": [
            "graph_acyclicity",
            "graph_roots",
            "graph_connectivity",
            "computed_depth",
            "transition_semantics",
            "global_child_idempotency",
        ],
        "not_checked": [
            "recovery_execution",
            "checkpoint_execution",
            "admission_authenticity",
            "admission_decision_binding",
        ],
        "requires_external_verification": {
            "admission_authenticity": "govengine",
            "admission_decision_binding": "govengine",
            "profile_transition_semantics": "profile",
        },
        "node_count": len(nodes),
        "edge_count": len(edge_ids),
        "root_node_id": roots[0],
    }


def _depth(value: Mapping[str, Any], kind: str) -> int:
    depth = value.get("depth")
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
        raise RExecOpValidationError(f"automation graph {kind} depth must be non-negative")
    return depth
