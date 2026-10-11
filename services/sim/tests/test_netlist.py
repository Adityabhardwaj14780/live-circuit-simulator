"""Netlist generation tests — structure, mapping fidelity and injection safety."""

from __future__ import annotations

from app.formula import parse
from app.graph import analyze_graph
from app.netlist import build_netlist
from app.schemas import GraphSpec

from .acceptance_graphs import edge, graph, node

from app.mapping import GROUND_NET, mapping_table


def plan_for(graph_dict: dict, revision: int = 1):
    spec = GraphSpec.model_validate(graph_dict)
    analysis = analyze_graph(spec)
    asts = {}
    literals = {}
    evaluable = [node_id for node_id in analysis.order if node_id not in analysis.cyclic and node_id not in analysis.blocked]
    evaluable = [
        node_id
        for node_id in evaluable
        if not any(issue.severity == "error" for issue in analysis.issues_for(node_id))
    ]
    for node_id in evaluable:
        node_obj = analysis.node_by_id[node_id]
        if node_obj.type == "formula":
            asts[node_id] = parse(node_obj.data["expression"])
        if node_obj.type == "number":
            literals[node_id] = float(node_obj.data["value"])
    return analysis, build_netlist(spec, analysis, revision=revision, evaluable=evaluable, asts=asts, literals=literals)


def test_netlist_ends_with_op_and_end() -> None:
    _, plan = plan_for(
        graph("basic", [node("n1", "number", {"value": 5}), node("d1", "display", {})], [edge("e1", "n1", "d1", "out", "in")])
    )
    lines = [line for line in plan.text.splitlines() if line.strip()]
    assert lines[-2:] == [".op", ".end"]
    assert lines[0].startswith("* live-node-graph-engine")


def test_number_and_arithmetic_device_lines() -> None:
    _, plan = plan_for(
        graph(
            "arith",
            [
                node("n1", "number", {"value": 2}),
                node("n2", "number", {"value": 3}),
                node("a1", "add", {}),
                node("m1", "multiply", {}),
                node("q1", "divide", {}),
            ],
            [
                edge("e1", "n1", "a1", "out", "a"),
                edge("e2", "n2", "a1", "out", "b"),
                edge("e3", "n1", "m1", "out", "a"),
                edge("e4", "n2", "m1", "out", "b"),
                edge("e5", "n1", "q1", "out", "a"),
                edge("e6", "n2", "q1", "out", "b"),
            ],
        )
    )
    assert plan.device_lines == [
        "V1 n1 0 DC 2",
        "V2 n2 0 DC 3",
        "B3 n3 0 V = V(n1) + V(n2)",
        "B4 n4 0 V = V(n1) * V(n2)",
        "B5 n5 0 V = V(n1) / V(n2)",
    ]
    assert plan.net_by_node == {"n1": "n1", "n2": "n2", "a1": "n3", "m1": "n4", "q1": "n5"}


def test_display_produces_no_device_and_reads_upstream_net() -> None:
    _, plan = plan_for(
        graph("display", [node("n1", "number", {"value": 7}), node("d1", "display", {})], [edge("e1", "n1", "d1", "out", "in")])
    )
    assert plan.device_count == 1
    assert plan.probe_only_nodes == ["d1"]
    assert plan.upstream_net["d1"] == "n1"
    assert "B" not in plan.text


def test_formula_is_rendered_with_generated_net_names_only() -> None:
    _, plan = plan_for(
        graph(
            "formula",
            [
                node("n1", "number", {"value": 4}),
                node("n2", "number", {"value": 6}),
                node("f1", "formula", {"expression": "a * b + 1"}),
            ],
            [edge("e1", "n1", "f1", "out", "a"), edge("e2", "n2", "f1", "out", "b")],
        )
    )
    assert plan.device_lines[-1] == "B3 n3 0 V = ((V(n1) * V(n2)) + 1)"


def test_user_text_never_becomes_a_deck_directive() -> None:
    """Node ids and graph names are attacker-controlled: they may only ever appear inside comments."""
    hostile_names = [
        "x\n.include /etc/passwd",
        "x\n.tran 1u 1m\n.control\nshell id\n.endc",
        "x\rend\n.end",
        "x\x00\x1b[31m",
    ]
    for hostile in hostile_names:
        graph_dict = graph(
            hostile,
            [node("n1", "number", {"value": 1}), node("d1", "display", {})],
            [edge("e1", "n1", "d1", "out", "in")],
        )
        _, plan = plan_for(graph_dict)

        directives = [line for line in plan.text.splitlines() if line.startswith(".")]
        assert directives == [".op", ".end"], f"unexpected directive from {hostile!r}: {directives}"
        assert plan.text.splitlines()[0].startswith("* ")
        assert plan.device_lines == ["V1 n1 0 DC 1"]
        assert "\n" not in "".join(plan.device_lines)
        assert "\r" not in plan.text and "\x00" not in plan.text

    assert GROUND_NET == "0"


def test_mapping_table_documents_every_node_type() -> None:
    table = {row["nodeType"] for row in mapping_table()}
    assert table == {"number", "add", "multiply", "divide", "formula", "display"}
    templates = {row["nodeType"]: row["template"] for row in mapping_table()}
    assert templates["number"] == "{name} {net} {ground} DC {value}"
    assert templates["add"] == "{name} {net} {ground} V = V({in_a}) + V({in_b})"
    assert templates["multiply"] == "{name} {net} {ground} V = V({in_a}) * V({in_b})"
    assert templates["divide"] == "{name} {net} {ground} V = V({in_a}) / V({in_b})"
    assert templates["formula"] == "{name} {net} {ground} V = {expression}"


def test_blocked_and_cyclic_nodes_are_excluded_from_the_deck() -> None:
    from .acceptance_graphs import ACCEPTANCE_BY_ID

    _, plan = plan_for(ACCEPTANCE_BY_ID["s7_cycle"]["graph"])
    assert plan.simulated_nodes == ["src"]
    assert plan.device_count == 1
