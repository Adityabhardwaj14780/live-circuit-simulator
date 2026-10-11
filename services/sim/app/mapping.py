"""
Node → netlist mapping.

This module is the *only* place that knows how a node type becomes a SPICE device, mirroring
docs/NETLIST-MAPPING.md. Changing a mapping means changing this table and the doc, nothing else.

    number   ->  V<k> <net> 0 DC <value>                    (independent DC voltage source)
    add      ->  B<k> <net> 0 V = V(<a>) + V(<b>)            (behavioural voltage source)
    multiply ->  B<k> <net> 0 V = V(<a>) * V(<b>)
    divide   ->  B<k> <net> 0 V = V(<a>) / V(<b>)            (a = numerator, b = denominator)
    formula  ->  B<k> <net> 0 V = <expression with V(net) refs>
    display  ->  no device; the display value is V(<upstream net>) read from the .op node table

One volt equals one unit of value: every node's output net carries the node's numeric result.
Ground is the reserved net "0".
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import Codes, NetlistError
from .formula import Ast, to_ngspice
from .schemas import Node

GROUND_NET = "0"
NET_PREFIX = "n"


@dataclass(frozen=True)
class DeviceSpec:
    """A single SPICE device line derived from a node."""

    node_id: str
    designator: str  # "V" | "B"
    template: str
    description: str


DEVICE_TEMPLATES: dict[str, DeviceSpec] = {
    "number": DeviceSpec("", "V", "{name} {net} {ground} DC {value}", "independent DC voltage source"),
    "add": DeviceSpec("", "B", "{name} {net} {ground} V = V({in_a}) + V({in_b})", "behavioural sum"),
    "multiply": DeviceSpec("", "B", "{name} {net} {ground} V = V({in_a}) * V({in_b})", "behavioural product"),
    "divide": DeviceSpec("", "B", "{name} {net} {ground} V = V({in_a}) / V({in_b})", "behavioural ratio"),
    "formula": DeviceSpec("", "B", "{name} {net} {ground} V = {expression}", "behavioural expression"),
}

# Node types that own an output net (i.e. that need a device in the deck).
NET_PRODUCING_TYPES = frozenset(DEVICE_TEMPLATES.keys())
# Node types that consume an upstream net without producing one.
PASSIVE_TYPES = frozenset({"display"})


def net_for(index: int) -> str:
    """Deterministic internal net name. User node ids are never interpolated into the deck."""
    return f"{NET_PREFIX}{index}"


def designator_for(node_index: int, node_type: str) -> str:
    """Unique SPICE element name, e.g. V1, B3. Uses the node's position in the evaluation order."""
    spec = DEVICE_TEMPLATES.get(node_type)
    if spec is None:
        raise NetlistError(f"no device mapping for node type {node_type!r}", Codes.NETLIST_ERROR)
    return f"{spec.designator}{node_index}"


def render_device(
    node: Node,
    *,
    node_index: int,
    net: str,
    ground: str,
    inputs: dict[str, str],
    ast: Ast | None,
    literal: float | None,
) -> tuple[str, str]:
    """Render one deck line for `node`. Returns (deck_line, description)."""
    spec = DEVICE_TEMPLATES.get(node.type)
    if spec is None:
        raise NetlistError(f"no device mapping for node type {node.type!r}", Codes.NETLIST_ERROR)
    name = designator_for(node_index, node.type)

    if node.type == "number":
        if literal is None:
            raise NetlistError(f'node "{node.id}" has no numeric literal', Codes.NON_NUMERIC_VALUE)
        line = spec.template.format(name=name, net=net, ground=ground, value=format_literal(literal))
        return line, spec.description

    if node.type == "formula":
        if ast is None:
            raise NetlistError(f'node "{node.id}" has no parsed expression', Codes.FORMULA_SYNTAX_ERROR)
        expression = to_ngspice(ast, inputs)
        line = spec.template.format(name=name, net=net, ground=ground, expression=expression)
        return line, spec.description

    # binary arithmetic nodes
    missing = [handle for handle in ("a", "b") if handle not in inputs]
    if missing:
        raise NetlistError(
            f'node "{node.id}" is missing net(s) for input(s): {", ".join(missing)}',
            Codes.MISSING_INPUT,
        )
    line = spec.template.format(name=name, net=net, ground=ground, in_a=inputs["a"], in_b=inputs["b"])
    return line, spec.description


def format_literal(value: float) -> str:
    if float(value).is_integer() and abs(value) < 1.0e15:
        return str(int(value))
    return repr(float(value))


def mapping_table() -> list[dict[str, object]]:
    """Machine-readable mapping table (used by GET /mapping and the README table)."""
    rows: list[dict[str, object]] = []
    for node_type, spec in DEVICE_TEMPLATES.items():
        rows.append(
            {
                "nodeType": node_type,
                "device": spec.designator,
                "template": " ".join(spec.template.split()),
                "description": spec.description,
            }
        )
    rows.append(
        {
            "nodeType": "display",
            "device": None,
            # identical to docs/NETLIST-MAPPING.md and netlistHints.ts — asserted by
            # tests/test_mapping_docs.py so the three copies cannot drift
            "template": "value = V({in_net})",
            "description": "display produces no device; its value is read from the operating-point results",
        }
    )
    return rows
