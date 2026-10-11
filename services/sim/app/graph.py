"""
Graph validation, connectivity rules and cycle detection (authoritative implementation).

The TypeScript mirror in packages/graph-contract/src/index.ts gives the UI/API fast feedback; the
checks here are the ones that decide what may reach ngspice. They are intentionally kept in a
separate module from netlist generation, formula parsing and process execution.

Rules enforced
--------------
* exactly one edge per input handle (first edge wins, later ones are rejected with DUPLICATE_INPUT_EDGE)
* one output may feed many inputs (fan-out is fine)
* self-connections rejected (SELF_LOOP)
* handles must exist on the node type (UNKNOWN_HANDLE)
* required inputs must be wired (MISSING_INPUT)
* number literals must be finite and within range (NON_NUMERIC_VALUE)
* cycles are detected with Tarjan SCC; descendants of a cycle are marked blocked, not cyclic
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .errors import Codes
from .schemas import Edge, GraphSpec, Node, NodeIssue

MAX_NODES = 200
MAX_EDGES = 500
NUMBER_MIN = -1.0e9
NUMBER_MAX = 1.0e9
FORMULA_MAX_LENGTH = 200


@dataclass(frozen=True)
class NodeSpec:
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    required_inputs: tuple[str, ...]
    number_data: dict[str, tuple[float, float]] = field(default_factory=dict)
    formula_data: tuple[str, ...] = ()
    asset: str = ""


# Single registry for the simulation service; test_contract_mirror.py asserts it matches graph.schema.json.
NODE_REGISTRY: dict[str, NodeSpec] = {
    "number": NodeSpec(inputs=(), outputs=("out",), required_inputs=(), number_data={"value": (NUMBER_MIN, NUMBER_MAX)}),
    "add": NodeSpec(inputs=("a", "b"), outputs=("out",), required_inputs=("a", "b")),
    "multiply": NodeSpec(inputs=("a", "b"), outputs=("out",), required_inputs=("a", "b")),
    "divide": NodeSpec(inputs=("a", "b"), outputs=("out",), required_inputs=("a", "b")),
    "formula": NodeSpec(inputs=("a", "b"), outputs=("out",), required_inputs=(), formula_data=("expression",)),
    "display": NodeSpec(inputs=("in",), outputs=(), required_inputs=("in",)),
}

NET_PRODUCING_TYPES = frozenset({"number", "add", "multiply", "divide", "formula"})
DISPLAY_TYPE = "display"


@dataclass(frozen=True)
class Connection:
    edge_id: str
    source_id: str
    source_handle: str
    target_id: str
    target_handle: str


@dataclass
class GraphAnalysis:
    node_by_id: dict[str, Node]
    duplicates: list[str]
    order: list[str]
    cyclic: list[str]
    blocked: list[str]
    connections: dict[tuple[str, str], Connection]
    rejected_edges: dict[str, str]  # edge id -> error code
    node_issues: dict[str, list[NodeIssue]]
    graph_issues: list[NodeIssue]
    outgoing: dict[str, list[str]]

    def issues_for(self, node_id: str) -> list[NodeIssue]:
        return self.node_issues.get(node_id, [])


def spec_for(node_type: str) -> NodeSpec | None:
    return NODE_REGISTRY.get(node_type)


def is_known_handle(node_type: str, direction: str, handle: str) -> bool:
    spec = NODE_REGISTRY.get(node_type)
    if spec is None:
        return False
    return handle in (spec.inputs if direction == "input" else spec.outputs)


def upstream_of(analysis: GraphAnalysis, node_id: str) -> list[str]:
    """Distinct upstream node ids for `node_id` (via accepted edges)."""
    return sorted({c.source_id for (target, _), c in analysis.connections.items() if target == node_id})


def analyze_graph(graph: GraphSpec, *, max_nodes: int = MAX_NODES, max_edges: int = MAX_EDGES) -> GraphAnalysis:
    node_issues: dict[str, list[NodeIssue]] = {}
    graph_issues: list[NodeIssue] = []
    node_by_id: dict[str, Node] = {}
    duplicates: list[str] = []

    if len(graph.nodes) > max_nodes:
        graph_issues.append(
            NodeIssue(code=Codes.GRAPH_SCHEMA_INVALID, message=f"graph has {len(graph.nodes)} nodes, limit is {max_nodes}")
        )
    if len(graph.edges) > max_edges:
        graph_issues.append(
            NodeIssue(code=Codes.GRAPH_SCHEMA_INVALID, message=f"graph has {len(graph.edges)} edges, limit is {max_edges}")
        )

    for node in graph.nodes:
        if node.id in node_by_id:
            duplicates.append(node.id)
            graph_issues.append(
                NodeIssue(code=Codes.GRAPH_SCHEMA_INVALID, message=f'duplicate node id "{node.id}"', severity="error")
            )
            continue
        node_by_id[node.id] = node
        issues: list[NodeIssue] = []
        spec = spec_for(node.type)
        if spec is None:
            issues.append(
                NodeIssue(
                    code=Codes.UNKNOWN_NODE_TYPE,
                    message=f'unknown node type "{node.type}"',
                    severity="error",
                )
            )
        else:
            issues.extend(_validate_node_data(node, spec))
        if issues:
            node_issues[node.id] = issues

    # ---- edge validation (first edge per input wins) -------------------------------------------------
    connections: dict[tuple[str, str], Connection] = {}
    rejected_edges: dict[str, str] = {}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_by_id}
    seen_edge_ids: set[str] = set()

    for edge in graph.edges:
        code = _reject_reason(edge, node_by_id, connections, seen_edge_ids)
        if code is not None:
            rejected_edges[edge.id] = code
            graph_issues.append(
                NodeIssue(
                    code=code,
                    message=_reject_message(code, edge, node_by_id),
                    severity="error",
                )
            )
            continue
        seen_edge_ids.add(edge.id)
        connection = Connection(edge.id, edge.source, edge.sourceHandle, edge.target, edge.targetHandle)
        connections[(edge.target, edge.targetHandle)] = connection
        outgoing.setdefault(edge.source, []).append(edge.target)

    # ---- required inputs -----------------------------------------------------------------------------
    for node_id, node in node_by_id.items():
        spec = spec_for(node.type)
        if spec is None:
            continue
        for handle in spec.required_inputs:
            if (node_id, handle) not in connections:
                node_issues.setdefault(node_id, []).append(
                    NodeIssue(
                        code=Codes.MISSING_INPUT,
                        message=f'node "{node_id}" ({node.type}) is missing input "{handle}"',
                        severity="error",
                        handle=handle,
                    )
                )

    # ---- cycles --------------------------------------------------------------------------------------
    cyclic, blocked, order = classify_topology(list(node_by_id.keys()), outgoing, connections)
    for node_id in cyclic:
        node_issues.setdefault(node_id, []).append(
            NodeIssue(
                code=Codes.CYCLE_DETECTED,
                message=f'node "{node_id}" is part of a feedback cycle; cycles cannot be simulated',
                severity="error",
            )
        )

    return GraphAnalysis(
        node_by_id=node_by_id,
        duplicates=duplicates,
        order=order,
        cyclic=cyclic,
        blocked=blocked,
        connections=connections,
        rejected_edges=rejected_edges,
        node_issues=node_issues,
        graph_issues=graph_issues,
        outgoing=outgoing,
    )


def _validate_node_data(node: Node, spec: NodeSpec) -> list[NodeIssue]:
    issues: list[NodeIssue] = []
    data = node.data or {}

    for field_name, (minimum, maximum) in spec.number_data.items():
        raw = data.get(field_name)
        if raw is None or raw == "":
            issues.append(
                NodeIssue(
                    code=Codes.GRAPH_SCHEMA_INVALID,
                    message=f'node "{node.id}" is missing required data.{field_name}',
                    handle=None,
                )
            )
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            issues.append(
                NodeIssue(
                    code=Codes.NON_NUMERIC_VALUE,
                    message=f'node "{node.id}" data.{field_name} must be a finite number',
                )
            )
            continue
        value = float(raw)
        if value != value or value in (float("inf"), float("-inf")):
            issues.append(
                NodeIssue(code=Codes.NON_NUMERIC_VALUE, message=f'node "{node.id}" data.{field_name} must be finite')
            )
        elif value < minimum or value > maximum:
            issues.append(
                NodeIssue(
                    code=Codes.NON_NUMERIC_VALUE,
                    message=f'node "{node.id}" data.{field_name}={value:g} is outside [{minimum:g}, {maximum:g}]',
                )
            )

    for field_name in spec.formula_data:
        raw = data.get(field_name)
        if raw is None or raw == "":
            issues.append(
                NodeIssue(
                    code=Codes.GRAPH_SCHEMA_INVALID,
                    message=f'node "{node.id}" is missing required data.{field_name}',
                )
            )
            continue
        if not isinstance(raw, str):
            issues.append(
                NodeIssue(code=Codes.FORMULA_SYNTAX_ERROR, message=f'node "{node.id}" data.{field_name} must be a string')
            )
        elif len(raw) > FORMULA_MAX_LENGTH:
            issues.append(
                NodeIssue(
                    code=Codes.FORMULA_SYNTAX_ERROR,
                    message=f'node "{node.id}" expression is {len(raw)} characters, limit is {FORMULA_MAX_LENGTH}',
                )
            )
    return issues


def _reject_reason(
    edge: Edge,
    node_by_id: dict[str, Node],
    connections: dict[tuple[str, str], Connection],
    seen_edge_ids: set[str],
) -> str | None:
    if edge.id in seen_edge_ids:
        return Codes.GRAPH_SCHEMA_INVALID
    source = node_by_id.get(edge.source)
    target = node_by_id.get(edge.target)
    if source is None or target is None:
        return Codes.GRAPH_SCHEMA_INVALID
    if edge.source == edge.target:
        return Codes.SELF_LOOP
    if spec_for(source.type) is None or spec_for(target.type) is None:
        return Codes.UNKNOWN_NODE_TYPE
    if not is_known_handle(source.type, "output", edge.sourceHandle):
        return Codes.UNKNOWN_HANDLE
    if not is_known_handle(target.type, "input", edge.targetHandle):
        return Codes.UNKNOWN_HANDLE
    if (edge.target, edge.targetHandle) in connections:
        return Codes.DUPLICATE_INPUT_EDGE
    return None


def _reject_message(code: str, edge: Edge, node_by_id: dict[str, Node]) -> str:
    source = node_by_id.get(edge.source)
    target = node_by_id.get(edge.target)
    if code == Codes.SELF_LOOP:
        return f'edge "{edge.id}" is a self-connection and was ignored'
    if code == Codes.UNKNOWN_HANDLE:
        return (
            f'edge "{edge.id}" uses an unknown handle '
            f"({getattr(source, 'type', '?')}.{edge.sourceHandle} -> {getattr(target, 'type', '?')}.{edge.targetHandle})"
        )
    if code == Codes.DUPLICATE_INPUT_EDGE:
        return f'edge "{edge.id}" was ignored: input "{edge.targetHandle}" of node "{edge.target}" is already connected'
    if code == Codes.UNKNOWN_NODE_TYPE:
        return f'edge "{edge.id}" touches a node with an unknown type and was ignored'
    return f'edge "{edge.id}" references a missing node or duplicate id and was ignored'


def classify_topology(
    node_ids: list[str],
    outgoing: dict[str, list[str]],
    connections: dict[tuple[str, str], Connection],
) -> tuple[list[str], list[str], list[str]]:
    """Return (cyclic, blocked, evaluation order).

    Cycle membership uses strongly connected components (Tarjan): a Kahn peel alone would also
    report every descendant of a cycle as cyclic.
    """
    accepted_out: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for connection in connections.values():
        accepted_out.setdefault(connection.source_id, []).append(connection.target_id)
    for key in accepted_out:
        accepted_out[key] = sorted(set(accepted_out[key]))

    component_of = _tarjan_scc(node_ids, accepted_out)
    sizes: dict[int, int] = {}
    for node_id in node_ids:
        component = component_of[node_id]
        sizes[component] = sizes.get(component, 0) + 1

    cyclic = sorted(node_id for node_id in node_ids if sizes[component_of[node_id]] > 1)

    blocked_set: set[str] = set()
    queue = list(cyclic)
    while queue:
        current = queue.pop(0)
        for child in accepted_out.get(current, []):
            if child in cyclic or child in blocked_set:
                continue
            blocked_set.add(child)
            queue.append(child)

    healthy = [node_id for node_id in sorted(node_ids) if node_id not in cyclic and node_id not in blocked_set]
    healthy_set = set(healthy)
    indegree = {node_id: 0 for node_id in healthy}
    for node_id in healthy:
        for child in accepted_out.get(node_id, []):
            if child in healthy_set:
                indegree[child] += 1

    ready = sorted(node_id for node_id in healthy if indegree[node_id] == 0)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for child in accepted_out.get(current, []):
            if child not in healthy_set:
                continue
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()

    return cyclic, sorted(blocked_set), order


def _tarjan_scc(node_ids: Iterable[str], out_adj: dict[str, list[str]]) -> dict[str, int]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    component_of: dict[str, int] = {}
    counter = 0
    component_count = 0

    for root in node_ids:
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        index[root] = counter
        low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)

        while work:
            node, cursor = work[-1]
            neighbours = out_adj.get(node, [])
            if cursor < len(neighbours):
                child = neighbours[cursor]
                work[-1] = (node, cursor + 1)
                if child not in index:
                    index[child] = counter
                    low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, 0))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component_of[member] = component_count
                    if member == node:
                        break
                component_count += 1

    return component_of
