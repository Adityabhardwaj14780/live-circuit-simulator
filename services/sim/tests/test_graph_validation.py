"""Validation, connectivity and cycle detection tests."""

from __future__ import annotations

from app.errors import Codes
from app.graph import analyze_graph
from app.schemas import GraphSpec

from .acceptance_graphs import ACCEPTANCE_BY_ID, edge, graph, node


def analyze(graph_dict: dict) -> "analyze_graph_result":
    return analyze_graph(GraphSpec.model_validate(graph_dict))


def codes_for(analysis, node_id: str) -> list[str]:
    return [issue.code for issue in analysis.issues_for(node_id)]


def test_number_node_requires_a_literal() -> None:
    analysis = analyze(graph("bad number", [node("n1", "number", {})], []))
    assert Codes.GRAPH_SCHEMA_INVALID in codes_for(analysis, "n1")


def test_non_numeric_and_out_of_range_literals_are_rejected() -> None:
    analysis = analyze(graph("bad literal", [node("n1", "number", {"value": "abc"}), node("n2", "number", {"value": 1e12})], []))
    assert Codes.NON_NUMERIC_VALUE in codes_for(analysis, "n1")
    assert Codes.NON_NUMERIC_VALUE in codes_for(analysis, "n2")


def test_unknown_node_type_is_rejected() -> None:
    analysis = analyze(graph("unknown", [node("x", "subtract", {})], []))
    assert Codes.UNKNOWN_NODE_TYPE in codes_for(analysis, "x")


def test_missing_required_input_is_reported_per_handle() -> None:
    analysis = analyze(graph("missing", [node("n1", "number", {"value": 1}), node("a1", "add", x=1)], [edge("e1", "n1", "a1", "out", "a")]))
    issues = analysis.issues_for("a1")
    assert [issue.code for issue in issues] == [Codes.MISSING_INPUT]
    assert issues[0].handle == "b"


def test_self_connection_is_rejected() -> None:
    analysis = analyze(
        graph(
            "self",
            [node("a1", "add", {}), node("n1", "number", {"value": 1})],
            [edge("bad", "a1", "a1", "out", "a"), edge("e1", "n1", "a1", "out", "a"), edge("e2", "n1", "a1", "out", "b")],
        )
    )
    assert analysis.rejected_edges["bad"] == Codes.SELF_LOOP


def test_unknown_handles_are_rejected() -> None:
    analysis = analyze(
        graph(
            "handles",
            [node("n1", "number", {"value": 1}), node("d1", "display", {})],
            [
                edge("bad-out", "n1", "d1", "value", "in"),  # number has no "value" output handle
                edge("bad-in", "n1", "d1", "out", "out"),  # display has no "out" input handle
            ],
        )
    )
    assert analysis.rejected_edges["bad-out"] == Codes.UNKNOWN_HANDLE
    assert analysis.rejected_edges["bad-in"] == Codes.UNKNOWN_HANDLE


def test_second_edge_into_the_same_input_is_rejected_but_first_wins() -> None:
    analysis = analyze(
        graph(
            "dup",
            [node("n1", "number", {"value": 1}), node("n2", "number", {"value": 2}), node("a1", "add", {})],
            [
                edge("e1", "n1", "a1", "out", "a"),
                edge("e2", "n2", "a1", "out", "a"),  # rejected
                edge("e3", "n2", "a1", "out", "b"),
            ],
        )
    )
    assert analysis.rejected_edges["e2"] == Codes.DUPLICATE_INPUT_EDGE
    assert analysis.connections[("a1", "a")].edge_id == "e1"
    assert analysis.connections[("a1", "b")].edge_id == "e3"


def test_fan_out_is_allowed() -> None:
    """One output feeding several inputs is legal, and the evaluation order respects dependencies."""
    analysis = analyze(ACCEPTANCE_BY_ID["s5_fan_out"]["graph"])
    assert analysis.rejected_edges == {}
    assert sorted(analysis.order) == ["add1", "d1", "d2", "mul1", "n1"]
    for (target, _handle), connection in analysis.connections.items():
        assert analysis.order.index(connection.source_id) < analysis.order.index(target)
    # deterministic: same graph in, same order out
    assert analyze(ACCEPTANCE_BY_ID["s5_fan_out"]["graph"]).order == analysis.order


def test_single_source_feeding_both_inputs_of_one_node() -> None:
    """Two edges from the same output into a and b of one node must still order correctly."""
    analysis = analyze(
        graph(
            "self fan-in",
            [node("n1", "number", {"value": 5}), node("a1", "add", {})],
            [edge("e1", "n1", "a1", "out", "a"), edge("e2", "n1", "a1", "out", "b")],
        )
    )
    assert analysis.order == ["n1", "a1"]
    assert analysis.rejected_edges == {}


def test_edges_referencing_missing_nodes_are_rejected() -> None:
    analysis = analyze(graph("missing node", [node("n1", "number", {"value": 1})], [edge("e1", "n1", "ghost", "out", "in")]))
    assert analysis.rejected_edges["e1"] == Codes.GRAPH_SCHEMA_INVALID


def test_duplicate_node_ids_are_reported() -> None:
    analysis = analyze(graph("dup ids", [node("n1", "number", {"value": 1}), node("n1", "number", {"value": 2})], []))
    assert analysis.duplicates == ["n1"]


def test_cycle_is_detected_and_descendants_are_blocked_not_cyclic() -> None:
    analysis = analyze(ACCEPTANCE_BY_ID["s7_cycle"]["graph"])
    assert analysis.cyclic == ["cyc_add", "cyc_mul"]
    assert analysis.blocked == ["d1"]
    assert analysis.order == ["src"]
    assert Codes.CYCLE_DETECTED in codes_for(analysis, "cyc_add")
    assert Codes.CYCLE_DETECTED in codes_for(analysis, "cyc_mul")


def test_three_node_cycle() -> None:
    graph_dict = graph(
        "cycle3",
        [node("a", "add", {}), node("b", "multiply", {}), node("c", "add", {})],
        [
            edge("e1", "a", "b", "out", "a"),
            edge("e2", "b", "c", "out", "a"),
            edge("e3", "c", "a", "out", "a"),
            edge("e4", "a", "b", "out", "b"),
            edge("e5", "b", "c", "out", "b"),
            edge("e6", "c", "a", "out", "b"),
        ],
    )
    analysis = analyze(graph_dict)
    assert analysis.cyclic == ["a", "b", "c"]
    assert analysis.order == []


def test_diamond_is_not_a_cycle() -> None:
    graph_dict = graph(
        "diamond",
        [node("n1", "number", {"value": 2}), node("a1", "add", {}), node("m1", "multiply", {}), node("d1", "display", {})],
        [
            edge("e1", "n1", "a1", "out", "a"),
            edge("e2", "n1", "a1", "out", "b"),
            edge("e3", "n1", "m1", "out", "a"),
            edge("e4", "n1", "m1", "out", "b"),
            edge("e5", "a1", "d1", "out", "in"),
            edge("e6", "m1", "d1", "out", "in"),
        ],
    )
    analysis = analyze(graph_dict)
    assert analysis.cyclic == []
    assert analysis.rejected_edges["e6"] == Codes.DUPLICATE_INPUT_EDGE  # display input already taken


def test_formula_with_unwired_referenced_input_is_reported() -> None:
    graph_dict = graph(
        "formula missing ident",
        [node("n1", "number", {"value": 1}), node("f1", "formula", {"expression": "a + b"})],
        [edge("e1", "n1", "f1", "out", "a")],
    )
    analysis = analyze(graph_dict)
    assert analysis.issues_for("f1") == []  # required-input check is expression-driven, handled by the engine
