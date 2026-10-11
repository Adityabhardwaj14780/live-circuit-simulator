"""
Netlist generation — the only module that writes SPICE syntax.

Deck layout (always ends with ``.op`` then ``.end`` as required):

    * live-node-graph-engine             <- line 1 is the SPICE title
    * graph: <name> (revision <rev>)
    * nodes: <n>, edges: <m>
    V1 n1 0 DC 2
    B2 n3 0 V = V(n1) + V(n2)
    ...
    .op
    .end

Net names (``n1``, ``n2``, ...) and element names (``V1``, ``B3``, ...) are generated internally, so
no user-provided text is ever interpolated into the deck; user node ids stay in the maps below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import Codes, NetlistError
from .formula import Ast
from .graph import NET_PRODUCING_TYPES, Connection, GraphAnalysis
from .mapping import DEVICE_TEMPLATES, GROUND_NET, net_for, render_device
from .schemas import GraphSpec

DECIMAL_PLACES = 12

# Only these characters may appear in text that is echoed into deck comments. Everything else
# (newlines above all — a newline would end the comment and let text become a SPICE directive)
# is replaced, so a hostile graph name can never inject a deck instruction.
SAFE_TEXT_RE = re.compile(r"[^A-Za-z0-9 ._:/+\-()\[\]=,]*")
MAX_TITLE_LENGTH = 80


def sanitize_title(text: str | None) -> str:
    """Make arbitrary user text safe to place inside a comment line of the deck."""
    if not text:
        return "untitled graph"
    flattened = SAFE_TEXT_RE.sub("", str(text).replace("\n", " ").replace("\r", " ").replace("\t", " "))
    flattened = " ".join(flattened.split())[:MAX_TITLE_LENGTH]
    return flattened or "untitled graph"


@dataclass
class NetlistPlan:
    text: str
    net_by_node: dict[str, str] = field(default_factory=dict)
    node_by_net: dict[str, str] = field(default_factory=dict)
    device_lines: list[str] = field(default_factory=list)
    simulated_nodes: list[str] = field(default_factory=list)
    probe_only_nodes: list[str] = field(default_factory=list)
    upstream_net: dict[str, str] = field(default_factory=dict)  # probe node id -> net of its input

    @property
    def device_count(self) -> int:
        return len(self.device_lines)


def build_netlist(
    graph: GraphSpec,
    analysis: GraphAnalysis,
    *,
    revision: int,
    evaluable: list[str],
    asts: dict[str, Ast],
    literals: dict[str, float],
) -> NetlistPlan:
    """Generate the deck for the evaluable part of the graph.

    `evaluable` must already be free of errored/blocked/cyclic nodes and sorted in evaluation order.
    Display nodes do not produce devices; they only record which net to read from the .op table.
    """
    plan = NetlistPlan(text="")
    net_index = 0

    for node_id in evaluable:
        connection_map = _input_nets(analysis, plan, node_id)
        node = analysis.node_by_id[node_id]

        if node.type in NET_PRODUCING_TYPES:
            net_index += 1
            net = net_for(net_index)
            line, _description = render_device(
                node,
                node_index=net_index,
                net=net,
                ground=GROUND_NET,
                inputs=connection_map,
                ast=asts.get(node_id),
                literal=literals.get(node_id),
            )
            plan.net_by_node[node_id] = net
            plan.node_by_net[net] = node_id
            plan.device_lines.append(line)
            plan.simulated_nodes.append(node_id)
        elif node.type == "display":
            upstream = _single_upstream_net(analysis, plan, node_id)
            if upstream is None:
                raise NetlistError(
                    f'display node "{node_id}" has no upstream net to read',
                    Codes.NETLIST_ERROR,
                )
            plan.upstream_net[node_id] = upstream
            plan.probe_only_nodes.append(node_id)
        else:  # pragma: no cover - unknown types are rejected before planning
            raise NetlistError(f'node "{node_id}" of type "{node.type}" cannot be simulated', Codes.NETLIST_ERROR)

    header = [
        f"* live-node-graph-engine | {sanitize_title(graph.name)}",
        f"* revision {revision} | nodes {len(graph.nodes)} | edges {len(graph.edges)}",
        f"* planned devices {len(plan.device_lines)} | probes {len(plan.probe_only_nodes)}",
    ]
    body = [*header, *plan.device_lines, ".op", ".end", ""]
    plan.text = "\n".join(body)
    return plan


def _input_nets(analysis: GraphAnalysis, plan: NetlistPlan, node_id: str) -> dict[str, str]:
    nets: dict[str, str] = {}
    for (target, handle), connection in analysis.connections.items():
        if target != node_id:
            continue
        upstream = plan.net_by_node.get(connection.source_id)
        if upstream is None:
            raise NetlistError(
                f'node "{node_id}" input "{handle}" is fed by node "{connection.source_id}" which has no net',
                Codes.NETLIST_ERROR,
            )
        nets[handle] = upstream
    return nets


def _single_upstream_net(analysis: GraphAnalysis, plan: NetlistPlan, node_id: str) -> str | None:
    connection: Connection | None = analysis.connections.get((node_id, "in"))
    if connection is None:
        return None
    return plan.net_by_node.get(connection.source_id)


def mapping_for_review() -> dict[str, str]:
    """Templates as documented (used by tests so the doc and code cannot drift)."""
    return {node_type: " ".join(spec.template.split()) for node_type, spec in DEVICE_TEMPLATES.items()}
