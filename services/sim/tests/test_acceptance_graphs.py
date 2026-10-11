"""
The eight acceptance scenarios, executed end-to-end: validation, netlist generation, a real
`ngspice -b` subprocess and output parsing.

Every scenario is one parametrised case, so a failure names the exact scenario. Skipped only when
ngspice is absent (it is installed in the Docker image and in CI).
"""

from __future__ import annotations

import pytest

from app.engine import evaluate_graph
from app.schemas import GraphSpec

from .acceptance_graphs import ACCEPTANCE_GRAPHS
from .conftest import HAS_NGSPICE

pytestmark = pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice is not installed")

IDS = [entry["id"] for entry in ACCEPTANCE_GRAPHS]


def run(entry: dict, settings, revision: int = 1):
    return evaluate_graph(GraphSpec.model_validate(entry["graph"]), revision=revision, settings=settings)


def by_id(response):
    return {result.nodeId: result for result in response.results}


@pytest.mark.parametrize("entry", ACCEPTANCE_GRAPHS, ids=IDS)
def test_acceptance_scenario(entry: dict, settings) -> None:
    response = run(entry, settings)
    results = by_id(response)

    assert response.status == entry["expectStatus"], f'{entry["id"]}: {[r.model_dump() for r in response.results]}'
    assert response.revision == 1

    # 1. values produced by real ngspice
    for node_id, expected in entry.get("expectValues", {}).items():
        result = results[node_id]
        assert result.status == "ok", f'{entry["id"]}/{node_id}: {result.status} {result.errorCode} {result.message}'
        assert result.value == pytest.approx(expected, abs=entry["expectValueTolerance"])

    # 2. node-specific error codes
    for node_id, code in entry.get("expectErrorCodes", {}).items():
        result = results[node_id]
        expected_status = "blocked" if code == "UPSTREAM_ERROR" else "error"
        assert result.status == expected_status, f'{entry["id"]}/{node_id}: {result.model_dump()}'
        assert result.errorCode == code, f'{entry["id"]}/{node_id}: got {result.errorCode}, want {code}'

    # 3. explicit per-node statuses (error vs blocked vs ok)
    for node_id, status in entry.get("expectStatuses", {}).items():
        assert results[node_id].status == status, f'{entry["id"]}/{node_id}: {results[node_id].model_dump()}'

    # 4. issues name the offending handle
    for node_id, handles in entry.get("expectIssueHandles", {}).items():
        reported = sorted(issue.handle for issue in results[node_id].issues if issue.handle)
        assert reported == sorted(handles), f'{entry["id"]}/{node_id}: {reported}'

    # 5. cycle / blocked classification coming from the engine
    if "expectCyclic" in entry:
        assert response.topology["cyclic"] == entry["expectCyclic"]
    if "expectBlocked" in entry:
        assert response.topology["blocked"] == entry["expectBlocked"]
        for node_id in entry["expectBlocked"]:
            assert results[node_id].status == "blocked"

    # 6. mapping fidelity + injection safety of the generated deck
    netlist = response.netlist or ""
    device_lines = [line for line in netlist.splitlines() if line[:1] in ("V", "B")]
    for needle in entry.get("expectNetlistContains", []):
        assert needle in netlist, f'{entry["id"]}: netlist is missing {needle!r}\n{netlist}'
    for needle in entry.get("expectNetlistExcludes", []):
        assert needle not in netlist, f'{entry["id"]}: netlist must not contain {needle!r}\n{netlist}'
    if "expectDeviceLines" in entry:
        assert device_lines == entry["expectDeviceLines"], f'{entry["id"]}: devices {device_lines}'

    # 7. every deck ngspice ran ends with .op then .end
    if netlist.strip():
        lines = [line for line in netlist.splitlines() if line.strip()]
        assert lines[-2:] == [".op", ".end"], f'{entry["id"]}: deck tail is {lines[-2:]}'


def test_all_eight_scenarios_are_present() -> None:
    assert len(ACCEPTANCE_GRAPHS) == 8
    assert [entry["id"] for entry in ACCEPTANCE_GRAPHS] == [
        "s1_number_display",
        "s2_add",
        "s3_multiply",
        "s4_divide",
        "s5_fan_out",
        "s6_missing_input",
        "s7_cycle",
        "s8_formula_safety",
    ]


def test_named_behaviours_are_covered() -> None:
    """Each behaviour the assignment calls out must be declared by at least one scenario."""
    covered = {behaviour for entry in ACCEPTANCE_GRAPHS for behaviour in entry["covers"]}
    for required in (
        "missing input detection",
        "division by zero",
        "cycle detection",
        "formula correctness",
        "malicious formula rejection",
    ):
        assert required in covered, f"no acceptance scenario covers {required!r}"


def test_ok_scenarios_really_ran_ngspice(settings) -> None:
    """Values must come from ngspice: every ok-scenario reports a version and a positive run time."""
    for entry in ACCEPTANCE_GRAPHS:
        response = run(entry, settings)
        assert response.ngspiceVersion and "ngspice" in response.ngspiceVersion.lower(), entry["id"]
        assert response.netlist is not None, entry["id"]
        if entry["expectStatus"] != "error":
            # scenarios with an evaluable subgraph must have executed a subprocess
            assert response.timings.ngspiceMs > 0.0, f'{entry["id"]}: no ngspice run time recorded'


def test_scenarios_are_generic_not_special_cased(settings) -> None:
    """An ad-hoc graph that appears in no fixture must execute correctly (no hard-coded graphs)."""
    graph = {
        "name": "ad-hoc",
        "nodes": [
            {"id": "alpha", "type": "number", "position": {"x": 0, "y": 0}, "data": {"value": 1.25}},
            {"id": "beta", "type": "number", "position": {"x": 0, "y": 90}, "data": {"value": 0.5}},
            {"id": "gal", "type": "divide", "position": {"x": 200, "y": 0}, "data": {}},
            {"id": "mix", "type": "formula", "position": {"x": 400, "y": 0}, "data": {"expression": "(a - b) * 2"}},
            {"id": "screen", "type": "display", "position": {"x": 600, "y": 0}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "alpha", "sourceHandle": "out", "target": "gal", "targetHandle": "a"},
            {"id": "e2", "source": "beta", "sourceHandle": "out", "target": "gal", "targetHandle": "b"},
            {"id": "e3", "source": "gal", "sourceHandle": "out", "target": "mix", "targetHandle": "a"},
            {"id": "e4", "source": "beta", "sourceHandle": "out", "target": "mix", "targetHandle": "b"},
            {"id": "e5", "source": "mix", "sourceHandle": "out", "target": "screen", "targetHandle": "in"},
        ],
    }
    response = evaluate_graph(GraphSpec.model_validate(graph), revision=7, settings=settings)
    results = by_id(response)
    assert response.status == "ok"
    assert results["gal"].value == pytest.approx(2.5)
    assert results["mix"].value == pytest.approx(4.0)  # (2.5 - 0.5) * 2
    assert results["screen"].value == pytest.approx(4.0)


def test_rejected_duplicate_edge_does_not_change_results(settings) -> None:
    """The second edge into an input is ignored and reported; the first still wins."""
    graph = {
        "name": "duplicate input",
        "nodes": [
            {"id": "one", "type": "number", "position": {"x": 0, "y": 0}, "data": {"value": 4}},
            {"id": "two", "type": "number", "position": {"x": 0, "y": 90}, "data": {"value": 100}},
            {"id": "sum", "type": "add", "position": {"x": 200, "y": 0}, "data": {}},
        ],
        "edges": [
            {"id": "keep", "source": "one", "sourceHandle": "out", "target": "sum", "targetHandle": "a"},
            {"id": "drop", "source": "two", "sourceHandle": "out", "target": "sum", "targetHandle": "a"},
            {"id": "b", "source": "two", "sourceHandle": "out", "target": "sum", "targetHandle": "b"},
        ],
    }
    response = evaluate_graph(GraphSpec.model_validate(graph), revision=2, settings=settings)
    results = by_id(response)
    assert results["sum"].value == pytest.approx(104.0)
    assert any(warning.code == "DUPLICATE_INPUT_EDGE" for warning in response.warnings)
