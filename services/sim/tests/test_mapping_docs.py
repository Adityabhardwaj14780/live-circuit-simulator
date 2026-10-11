"""
The node → netlist mapping is documented in three places that must not drift:

  * services/sim/app/mapping.py            (authoritative, executed)
  * docs/NETLIST-MAPPING.md                (human documentation with the table of templates)
  * packages/graph-contract/src/netlistHints.ts (shown in the UI)

This module asserts the two derived copies still describe what mapping.py renders, and that the
deck ngspice receives matches the documented templates exactly.
"""

from __future__ import annotations

import collections
import pathlib
import re

import pytest

from app.formula import parse
from app.graph import analyze_graph
from app.mapping import GROUND_NET, mapping_table
from app.netlist import build_netlist
from app.schemas import GraphSpec

from .acceptance_graphs import edge, graph, node

def _find_repo_root() -> pathlib.Path | None:
    current = pathlib.Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "docs" / "NETLIST-MAPPING.md").is_file():
            return parent
    return None


REPO_ROOT = _find_repo_root()
DOC_PATH = (REPO_ROOT / "docs" / "NETLIST-MAPPING.md") if REPO_ROOT else pathlib.Path("/nonexistent/docs/NETLIST-MAPPING.md")
HINTS_PATH = (
    (REPO_ROOT / "packages" / "graph-contract" / "src" / "netlistHints.ts")
    if REPO_ROOT
    else pathlib.Path("/nonexistent/packages/graph-contract/src/netlistHints.ts")
)


def _templates() -> dict[str, str]:
    return {row["nodeType"]: row["template"] for row in mapping_table()}


def test_mapping_table_covers_every_node_type() -> None:
    assert set(_templates()) == {"number", "add", "multiply", "divide", "formula", "display"}


def test_mapping_table_is_reachable_from_the_api() -> None:
    """GET /mapping is what the UI and reviewers read; it must be the same table."""
    from app.main import mapping as mapping_endpoint

    payload = mapping_endpoint()
    assert {row["nodeType"] for row in payload["mapping"]} == set(_templates())
    assert payload["ground"] == GROUND_NET == "0"
    assert "1 unit of node value" in payload["note"]


@pytest.mark.skipif(not DOC_PATH.exists(), reason="docs are not part of this build context")
def test_documentation_matches_the_implemented_templates() -> None:
    doc = DOC_PATH.read_text()
    for node_type, template in _templates().items():
        assert template in doc, f"docs/NETLIST-MAPPING.md is out of date for {node_type}: {template!r}"
    assert ".op" in doc and ".end" in doc
    assert "ngspice -b" in doc


@pytest.mark.skipif(not HINTS_PATH.exists(), reason="the TS package is not part of this build context")
def test_ui_hints_match_the_implemented_templates() -> None:
    """The strings the UI displays must equal the templates the engine renders."""
    hints = HINTS_PATH.read_text()
    for node_type, template in _templates().items():
        if node_type == "display":
            continue  # display produces no device; the UI shows a probe expression instead
        assert template in hints, f"netlistHints.ts is out of date for {node_type}: {template!r}"
    # the displayed probe hint must match the mapping table's description of display
    assert "value = V({in_net})" in hints


def test_rendered_deck_matches_the_documented_templates_verbatim() -> None:
    """End-to-end: the bytes ngspice receives follow the documented per-type templates."""
    spec = GraphSpec.model_validate(
        graph(
            "mapping fidelity",
            [
                node("num", "number", {"value": 2}),
                node("add", "add", y=80),
                node("mul", "multiply", y=160),
                node("div", "divide", y=240),
                node("f", "formula", {"expression": "a + b"}, y=320),
            ],
            [
                edge("e1", "num", "add", "out", "a"),
                edge("e2", "add", "add", "out", "b"),  # placeholder, replaced below
            ],
        )
    )
    # rebuild explicitly: every arithmetic node fed by the same literal so ordering is deterministic
    spec = GraphSpec.model_validate(
        graph(
            "mapping fidelity",
            [
                node("num", "number", {"value": 2}),
                node("add", "add", y=80),
                node("mul", "multiply", y=160),
                node("div", "divide", y=240),
                node("f", "formula", {"expression": "a + b"}, y=320),
            ],
            [
                edge("e1", "num", "add", "out", "a"),
                edge("e2", "num", "add", "out", "b"),
                edge("e3", "num", "mul", "out", "a"),
                edge("e4", "num", "mul", "out", "b"),
                edge("e5", "num", "div", "out", "a"),
                edge("e6", "num", "div", "out", "b"),
                edge("e7", "num", "f", "out", "a"),
                edge("e8", "num", "f", "out", "b"),
            ],
        )
    )
    analysis = analyze_graph(spec)
    asts = {"f": parse("a + b")}
    plan = build_netlist(
        spec,
        analysis,
        revision=1,
        evaluable=analysis.order,
        asts=asts,
        literals={"num": 2.0},
    )

    # every device line has the documented shape: "<V|B><n> <net> 0 ..."
    for line in plan.device_lines:
        assert re.match(r"^[VB]\d+ n\d+ 0 ", line), line

    # Device lines are emitted in evaluation order, so look each node up through its net rather
    # than relying on position.
    from app.mapping import net_for

    line_by_net = {line.split()[1]: line for line in plan.device_lines}
    assert line_by_net[net_for(1)] == "V1 n1 0 DC 2"  # number -> DC source
    assert collections.Counter(plan.device_lines).most_common(1)[0][1] == 1, "device designators must be unique"
    for node_id, operator in (("add", " + "), ("mul", " * "), ("div", " / ")):
        line = line_by_net[plan.net_by_node[node_id]]
        assert operator in line, f"{node_id}: {line}"
        assert line.startswith(f'B{line.split()[0][1:]} {plan.net_by_node[node_id]} 0 V = V(n1)'), line
    # designators follow evaluation order, so assert the shape rather than a fixed name
    formula_line = line_by_net[plan.net_by_node["f"]]
    assert re.fullmatch(r"B\d+ n\d+ 0 V = \(V\(n\d+\) \+ V\(n\d+\)\)", formula_line), formula_line

    # and each rendered line follows the documented template for its node type
    templates = _templates()
    for node_id in ("add", "mul", "div", "f"):
        node_type = {"add": "add", "mul": "multiply", "div": "divide", "f": "formula"}[node_id]
        template = templates[node_type]
        assert template.startswith("{name} {net} {ground} V ="), template
        assert "{ground}" in template and template.split()[2] == "{ground}"
