"""
Direct verification of the assignment's exact eight acceptance graphs (Pages 3-4 of the specification).

Acceptance graphs:
#1: Number 2 and Number 3 -> Add -> Display => Display = 5
#2: Graph 1's Add -> Multiply with Number 4 -> Display => Display = 20
#3: One Number 7 wired into both inputs of Multiply => Display = 49
#4: Number 10 / Number 0 -> Display => Divide node shows the error; Display shows blocked, not an old value
#5: Add with only one input wired => That Add node is highlighted (MISSING_INPUT)
#6: Add -> Multiply -> back into the same Add => Loop is flagged on the nodes involved (CYCLE_DETECTED)
#7: Formula a * 2 + b with a = 3, b = 4 => Display = 10
#8: Formula a + b followed by a new line and .control shell ls => Rejected with a clear message; nothing executes
"""

from __future__ import annotations

import pytest

from app.engine import evaluate_graph
from app.errors import Codes
from app.schemas import GraphSpec

from .conftest import HAS_NGSPICE

requires_ngspice = pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice is not installed on the host")


def _by_id(response):
    return {result.nodeId: result for result in response.results}


# --------------------------------------------------------------------------------------------------
# Acceptance Graph 1: Number 2 and Number 3 -> Add -> Display => Display = 5
# --------------------------------------------------------------------------------------------------
@requires_ngspice
def test_acceptance_graph_1_add(settings) -> None:
    graph = {
        "name": "Acceptance Graph 1",
        "nodes": [
            {"id": "n1", "type": "number", "position": {"x": 0, "y": 0}, "data": {"value": 2}},
            {"id": "n2", "type": "number", "position": {"x": 0, "y": 100}, "data": {"value": 3}},
            {"id": "add1", "type": "add", "position": {"x": 200, "y": 50}, "data": {}},
            {"id": "d1", "type": "display", "position": {"x": 400, "y": 50}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "sourceHandle": "out", "target": "add1", "targetHandle": "a"},
            {"id": "e2", "source": "n2", "sourceHandle": "out", "target": "add1", "targetHandle": "b"},
            {"id": "e3", "source": "add1", "sourceHandle": "out", "target": "d1", "targetHandle": "in"},
        ],
    }
    response = evaluate_graph(GraphSpec.model_validate(graph), revision=1, settings=settings)
    results = _by_id(response)
    assert response.status == "ok"
    assert results["n1"].value == pytest.approx(2.0)
    assert results["n2"].value == pytest.approx(3.0)
    assert results["add1"].value == pytest.approx(5.0)
    assert results["d1"].value == pytest.approx(5.0)


# --------------------------------------------------------------------------------------------------
# Acceptance Graph 2: Graph 1's Add -> Multiply with Number 4 -> Display => Display = 20
# --------------------------------------------------------------------------------------------------
@requires_ngspice
def test_acceptance_graph_2_chained_multiply(settings) -> None:
    graph = {
        "name": "Acceptance Graph 2",
        "nodes": [
            {"id": "n1", "type": "number", "position": {"x": 0, "y": 0}, "data": {"value": 2}},
            {"id": "n2", "type": "number", "position": {"x": 0, "y": 100}, "data": {"value": 3}},
            {"id": "add1", "type": "add", "position": {"x": 200, "y": 50}, "data": {}},
            {"id": "n4", "type": "number", "position": {"x": 200, "y": 180}, "data": {"value": 4}},
            {"id": "mul1", "type": "multiply", "position": {"x": 400, "y": 100}, "data": {}},
            {"id": "d1", "type": "display", "position": {"x": 600, "y": 100}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "sourceHandle": "out", "target": "add1", "targetHandle": "a"},
            {"id": "e2", "source": "n2", "sourceHandle": "out", "target": "add1", "targetHandle": "b"},
            {"id": "e3", "source": "add1", "sourceHandle": "out", "target": "mul1", "targetHandle": "a"},
            {"id": "e4", "source": "n4", "sourceHandle": "out", "target": "mul1", "targetHandle": "b"},
            {"id": "e5", "source": "mul1", "sourceHandle": "out", "target": "d1", "targetHandle": "in"},
        ],
    }
    response = evaluate_graph(GraphSpec.model_validate(graph), revision=2, settings=settings)
    results = _by_id(response)
    assert response.status == "ok"
    assert results["add1"].value == pytest.approx(5.0)
    assert results["mul1"].value == pytest.approx(20.0)
    assert results["d1"].value == pytest.approx(20.0)


# --------------------------------------------------------------------------------------------------
# Acceptance Graph 3: One Number 7 wired into both inputs of Multiply => Display = 49
# --------------------------------------------------------------------------------------------------
@requires_ngspice
def test_acceptance_graph_3_same_source_both_inputs(settings) -> None:
    graph = {
        "name": "Acceptance Graph 3",
        "nodes": [
            {"id": "n7", "type": "number", "position": {"x": 0, "y": 50}, "data": {"value": 7}},
            {"id": "mul1", "type": "multiply", "position": {"x": 200, "y": 50}, "data": {}},
            {"id": "d1", "type": "display", "position": {"x": 400, "y": 50}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "n7", "sourceHandle": "out", "target": "mul1", "targetHandle": "a"},
            {"id": "e2", "source": "n7", "sourceHandle": "out", "target": "mul1", "targetHandle": "b"},
            {"id": "e3", "source": "mul1", "sourceHandle": "out", "target": "d1", "targetHandle": "in"},
        ],
    }
    response = evaluate_graph(GraphSpec.model_validate(graph), revision=3, settings=settings)
    results = _by_id(response)
    assert response.status == "ok"
    assert results["n7"].value == pytest.approx(7.0)
    assert results["mul1"].value == pytest.approx(49.0)
    assert results["d1"].value == pytest.approx(49.0)


# --------------------------------------------------------------------------------------------------
# Acceptance Graph 4: Number 10 / Number 0 -> Display
# Divide node shows error; Display shows blocked, not an old value
# --------------------------------------------------------------------------------------------------
def test_acceptance_graph_4_divide_by_zero(settings) -> None:
    graph = {
        "name": "Acceptance Graph 4",
        "nodes": [
            {"id": "n10", "type": "number", "position": {"x": 0, "y": 0}, "data": {"value": 10}},
            {"id": "n0", "type": "number", "position": {"x": 0, "y": 100}, "data": {"value": 0}},
            {"id": "div1", "type": "divide", "position": {"x": 200, "y": 50}, "data": {}},
            {"id": "d1", "type": "display", "position": {"x": 400, "y": 50}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "n10", "sourceHandle": "out", "target": "div1", "targetHandle": "a"},
            {"id": "e2", "source": "n0", "sourceHandle": "out", "target": "div1", "targetHandle": "b"},
            {"id": "e3", "source": "div1", "sourceHandle": "out", "target": "d1", "targetHandle": "in"},
        ],
    }
    response = evaluate_graph(GraphSpec.model_validate(graph), revision=4, settings=settings)
    results = _by_id(response)

    # Divide node must show the error
    assert results["div1"].status == "error"
    assert results["div1"].errorCode == Codes.DIVIDE_BY_ZERO
    assert results["div1"].value is None

    # Display node must show blocked, not an old or silent zero value
    assert results["d1"].status == "blocked"
    assert results["d1"].errorCode == Codes.UPSTREAM_ERROR
    assert results["d1"].value is None

    # Deck must not contain a division line
    netlist = response.netlist or ""
    assert all("/" not in line for line in netlist.splitlines() if line.startswith("B"))


# --------------------------------------------------------------------------------------------------
# Acceptance Graph 5: Add with only one input wired => That Add node is highlighted
# --------------------------------------------------------------------------------------------------
def test_acceptance_graph_5_add_unwired_input(settings) -> None:
    graph = {
        "name": "Acceptance Graph 5",
        "nodes": [
            {"id": "n1", "type": "number", "position": {"x": 0, "y": 0}, "data": {"value": 5}},
            {"id": "add1", "type": "add", "position": {"x": 200, "y": 0}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "sourceHandle": "out", "target": "add1", "targetHandle": "a"},
        ],
    }
    response = evaluate_graph(GraphSpec.model_validate(graph), revision=5, settings=settings)
    results = _by_id(response)

    assert results["add1"].status == "error"
    assert results["add1"].errorCode == Codes.MISSING_INPUT
    assert [issue.handle for issue in results["add1"].issues if issue.handle] == ["b"]
    assert "b" in (results["add1"].message or "")


# --------------------------------------------------------------------------------------------------
# Acceptance Graph 6: Add -> Multiply -> back into the same Add => Loop flagged on nodes involved
# --------------------------------------------------------------------------------------------------
def test_acceptance_graph_6_loop_flagged(settings) -> None:
    graph = {
        "name": "Acceptance Graph 6",
        "nodes": [
            {"id": "src", "type": "number", "position": {"x": 0, "y": 0}, "data": {"value": 1}},
            {"id": "add1", "type": "add", "position": {"x": 200, "y": 0}, "data": {}},
            {"id": "mul1", "type": "multiply", "position": {"x": 400, "y": 0}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "src", "sourceHandle": "out", "target": "add1", "targetHandle": "a"},
            {"id": "e2", "source": "mul1", "sourceHandle": "out", "target": "add1", "targetHandle": "b"},
            {"id": "e3", "source": "add1", "sourceHandle": "out", "target": "mul1", "targetHandle": "a"},
            {"id": "e4", "source": "src", "sourceHandle": "out", "target": "mul1", "targetHandle": "b"},
        ],
    }
    response = evaluate_graph(GraphSpec.model_validate(graph), revision=6, settings=settings)
    results = _by_id(response)

    # Loop is flagged on both nodes involved in the cycle
    assert sorted(response.topology["cyclic"]) == ["add1", "mul1"]
    assert results["add1"].status == "error"
    assert results["add1"].errorCode == Codes.CYCLE_DETECTED
    assert results["mul1"].status == "error"
    assert results["mul1"].errorCode == Codes.CYCLE_DETECTED


# --------------------------------------------------------------------------------------------------
# Acceptance Graph 7: Formula a * 2 + b with a = 3, b = 4 => Display = 10
# --------------------------------------------------------------------------------------------------
@requires_ngspice
def test_acceptance_graph_7_formula_valid(settings) -> None:
    graph = {
        "name": "Acceptance Graph 7",
        "nodes": [
            {"id": "n_a", "type": "number", "position": {"x": 0, "y": 0}, "data": {"value": 3}},
            {"id": "n_b", "type": "number", "position": {"x": 0, "y": 100}, "data": {"value": 4}},
            {"id": "f1", "type": "formula", "position": {"x": 200, "y": 50}, "data": {"expression": "a * 2 + b"}},
            {"id": "d1", "type": "display", "position": {"x": 400, "y": 50}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "n_a", "sourceHandle": "out", "target": "f1", "targetHandle": "a"},
            {"id": "e2", "source": "n_b", "sourceHandle": "out", "target": "f1", "targetHandle": "b"},
            {"id": "e3", "source": "f1", "sourceHandle": "out", "target": "d1", "targetHandle": "in"},
        ],
    }
    response = evaluate_graph(GraphSpec.model_validate(graph), revision=7, settings=settings)
    results = _by_id(response)

    assert response.status == "ok"
    assert results["n_a"].value == pytest.approx(3.0)
    assert results["n_b"].value == pytest.approx(4.0)
    assert results["f1"].value == pytest.approx(10.0)
    assert results["d1"].value == pytest.approx(10.0)


# --------------------------------------------------------------------------------------------------
# Acceptance Graph 8: Formula a + b followed by newline and .control shell ls
# Rejected with a clear message; nothing executes
# --------------------------------------------------------------------------------------------------
def test_acceptance_graph_8_unsafe_formula_rejected(settings) -> None:
    malicious_expr = "a + b\n.control\nshell ls"
    graph = {
        "name": "Acceptance Graph 8",
        "nodes": [
            {"id": "n1", "type": "number", "position": {"x": 0, "y": 0}, "data": {"value": 1}},
            {"id": "n2", "type": "number", "position": {"x": 0, "y": 100}, "data": {"value": 2}},
            {"id": "f1", "type": "formula", "position": {"x": 200, "y": 50}, "data": {"expression": malicious_expr}},
            {"id": "d1", "type": "display", "position": {"x": 400, "y": 50}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "sourceHandle": "out", "target": "f1", "targetHandle": "a"},
            {"id": "e2", "source": "n2", "sourceHandle": "out", "target": "f1", "targetHandle": "b"},
            {"id": "e3", "source": "f1", "sourceHandle": "out", "target": "d1", "targetHandle": "in"},
        ],
    }
    response = evaluate_graph(GraphSpec.model_validate(graph), revision=8, settings=settings)
    results = _by_id(response)

    # Formula node must be rejected with a clear message
    assert results["f1"].status == "error"
    assert results["f1"].errorCode == Codes.FORMULA_DISALLOWED_TOKEN
    assert "not allowed" in (results["f1"].message or "").lower()

    # Downstream display must be blocked
    assert results["d1"].status == "blocked"
    assert results["d1"].errorCode == Codes.UPSTREAM_ERROR

    # The payload MUST NEVER reach any deck line or comment
    netlist = response.netlist or ""
    assert ".control" not in netlist
    assert "shell" not in netlist
    assert "ls" not in netlist
