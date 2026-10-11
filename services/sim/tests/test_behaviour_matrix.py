"""
Focused behaviour tests for the properties the acceptance scenarios rely on.

These complement (not duplicate) tests/acceptance_graphs.py: here each property is isolated and
asserted on its own, in particular the ones that are easy to get subtly wrong:

  * missing inputs are attributed to the exact node and handle
  * blocked state propagates transitively (3+ hops)
  * a graph that is entirely cyclic spawns NO ngspice subprocess at all
  * a timeout clears every value and the service recovers on the next request
  * rejected nodes never contribute a device to the deck
"""

from __future__ import annotations

import stat
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.engine import evaluate_graph
from app.errors import Codes
from app.schemas import GraphSpec

from .acceptance_graphs import edge, graph, node
from .conftest import HAS_NGSPICE

client = TestClient(main_module.app)
needs_ngspice = pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice is not installed")


def run(graph_dict: dict, settings, revision: int = 1):
    return evaluate_graph(GraphSpec.model_validate(graph_dict), revision=revision, settings=settings)


def by_id(response):
    return {result.nodeId: result for result in response.results}


# --------------------------------------------------------------------------------------- inputs
@needs_ngspice
def test_missing_input_names_the_node_and_the_handle(settings) -> None:
    graph_dict = graph(
        "missing input",
        [node("src", "number", {"value": 7}), node("adder", "add", x=200), node("screen", "display", x=400)],
        [edge("e1", "src", "adder", "out", "a"), edge("e2", "adder", "screen", "out", "in")],
    )
    response = run(graph_dict, settings)
    results = by_id(response)

    assert results["adder"].status == "error"
    assert results["adder"].errorCode == Codes.MISSING_INPUT
    assert "b" in (results["adder"].message or "")
    assert [issue.handle for issue in results["adder"].issues if issue.handle] == ["b"]
    assert results["screen"].status == "blocked"
    assert results["screen"].errorCode == Codes.UPSTREAM_ERROR
    assert results["screen"].value is None
    assert results["src"].status == "ok"  # the healthy part still simulates
    assert results["src"].value == pytest.approx(7.0)
    # the incomplete node contributes nothing to the deck
    assert all(line.startswith("V") for line in (response.netlist or "").splitlines() if line[:1] in ("V", "B"))


@needs_ngspice
def test_blocked_state_propagates_transitively(settings) -> None:
    """adder fails -> b1 -> b2 -> b3 must all be blocked, with no values leaking through."""
    graph_dict = graph(
        "long chain",
        [
            node("src", "number", {"value": 1}),
            node("adder", "add"),
            node("b1", "add", x=150),
            node("b2", "multiply", x=300),
            node("b3", "add", x=450),
            node("screen", "display", x=600),
        ],
        [
            edge("e1", "src", "adder", "out", "a"),  # adder.b deliberately unwired
            edge("e2", "adder", "b1", "out", "a"),
            edge("e3", "src", "b1", "out", "b"),
            edge("e4", "b1", "b2", "out", "a"),
            edge("e5", "src", "b2", "out", "b"),
            edge("e6", "b2", "b3", "out", "a"),
            edge("e7", "src", "b3", "out", "b"),
            edge("e8", "b3", "screen", "out", "in"),
        ],
    )
    results = by_id(run(graph_dict, settings))
    for node_id in ("b1", "b2", "b3", "screen"):
        assert results[node_id].status == "blocked", f"{node_id}: {results[node_id].model_dump()}"
        assert results[node_id].errorCode == Codes.UPSTREAM_ERROR
        assert results[node_id].value is None
    assert results["adder"].status == "error"
    assert results["src"].status == "ok"
    assert results["b1"].inputStates.get("a") == "error"


# ---------------------------------------------------------------------------------------- cycles
@needs_ngspice
def test_fully_cyclic_graph_runs_no_subprocess(settings) -> None:
    """Nothing evaluable => no ngspice process at all (no wasted work, no chance of a bogus value)."""
    graph_dict = graph(
        "pure cycle",
        [node("a", "add"), node("b", "multiply", x=200)],
        [
            edge("e1", "a", "b", "out", "a"),
            edge("e2", "b", "a", "out", "a"),
            edge("e3", "a", "b", "out", "b"),
            edge("e4", "b", "a", "out", "b"),
        ],
    )
    response = run(graph_dict, settings)
    results = by_id(response)

    assert response.status == "error"
    assert response.timings.ngspiceMs == 0.0, "a fully cyclic graph must not spawn ngspice"
    assert sorted(response.topology["cyclic"]) == ["a", "b"]
    assert results["a"].errorCode == Codes.CYCLE_DETECTED
    assert results["b"].errorCode == Codes.CYCLE_DETECTED
    assert all(result.value is None for result in response.results)


@needs_ngspice
def test_cycle_with_a_healthy_branch_still_simulates_the_branch(settings) -> None:
    graph_dict = graph(
        "cycle plus branch",
        [
            node("src", "number", {"value": 3}),
            node("good", "multiply", x=200, y=200),
            node("a", "add", x=200),
            node("b", "multiply", x=400),
        ],
        [
            edge("e1", "src", "good", "out", "a"),
            edge("e2", "src", "good", "out", "b"),
            edge("e3", "a", "b", "out", "a"),
            edge("e4", "b", "a", "out", "a"),
            edge("e5", "a", "b", "out", "b"),
            edge("e6", "b", "a", "out", "b"),
        ],
    )
    response = run(graph_dict, settings)
    results = by_id(response)

    assert results["good"].status == "ok"
    assert results["good"].value == pytest.approx(9.0)
    assert results["a"].errorCode == Codes.CYCLE_DETECTED
    assert results["b"].errorCode == Codes.CYCLE_DETECTED
    devices = [line for line in (response.netlist or "").splitlines() if line[:1] in ("V", "B")]
    assert len(devices) == 2  # the source and the healthy multiply only


# ----------------------------------------------------------------------------- divide by zero
@needs_ngspice
def test_divide_by_zero_is_caught_before_ngspice(settings) -> None:
    graph_dict = graph(
        "divide by zero",
        [node("ten", "number", {"value": 10}), node("zero", "number", {"value": 0}), node("q", "divide"), node("screen", "display")],
        [
            edge("e1", "ten", "q", "out", "a"),
            edge("e2", "zero", "q", "out", "b"),
            edge("e3", "q", "screen", "out", "in"),
        ],
    )
    response = run(graph_dict, settings)
    results = by_id(response)

    assert results["q"].status == "error"
    assert results["q"].errorCode == Codes.DIVIDE_BY_ZERO
    assert "0" in (results["q"].message or "")
    assert results["screen"].status == "blocked"
    # the denominator never reaches ngspice, which would have returned 1e+33 without complaining
    devices = [line for line in (response.netlist or "").splitlines() if line[:1] in ("V", "B")]
    assert all("/" not in line for line in devices)


# ---------------------------------------------------------------------------- formula rejection
@needs_ngspice
@pytest.mark.parametrize(
    "expression",
    ["__import__('os').system('id')", "a ** b", "a; b", "sqrt(a)", "a % b", "a + b\n.control\nshell ls"],
)
def test_malicious_or_unsupported_formulas_never_reach_the_deck(expression: str, settings) -> None:
    graph_dict = graph(
        "hostile formula",
        [
            node("n1", "number", {"value": 2}),
            node("n2", "number", {"value": 3}),
            node("f", "formula", {"expression": expression}),
            node("ok", "add"),
            node("screen", "display"),
        ],
        [
            edge("e1", "n1", "f", "out", "a"),
            edge("e2", "n2", "f", "out", "b"),
            edge("e3", "n1", "ok", "out", "a"),
            edge("e4", "n2", "ok", "out", "b"),
            edge("e5", "f", "screen", "out", "in"),
        ],
    )
    response = run(graph_dict, settings)
    results = by_id(response)

    assert results["f"].status == "error"
    assert results["f"].errorCode in (Codes.FORMULA_DISALLOWED_TOKEN, Codes.FORMULA_SYNTAX_ERROR)
    assert results["screen"].status == "blocked"
    # a rejected expression is dropped, while the rest of the graph keeps working
    assert results["ok"].status == "ok"
    assert results["ok"].value == pytest.approx(5.0)
    assert expression not in (response.netlist or "")


# --------------------------------------------------------------- timeouts, crashes and recovery
def _fake_binary(tmp_path, body: str) -> str:
    path = tmp_path / "fake-ngspice"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_timeout_clears_values_and_service_recovers(settings, tmp_path) -> None:
    """A hung ngspice is killed; the next request runs normally (no sticky failure, no stale value)."""
    slow = replace(settings, ngspice_bin=_fake_binary(tmp_path, "sleep 30"), timeout_seconds=0.5)
    graph_dict = graph(
        "timeout",
        [node("n1", "number", {"value": 8}), node("m", "multiply"), node("screen", "display")],
        [
            edge("e1", "n1", "m", "out", "a"),
            edge("e2", "n1", "m", "out", "b"),
            edge("e3", "m", "screen", "out", "in"),
        ],
    )

    timed_out = run(graph_dict, slow, revision=1)
    results = by_id(timed_out)
    assert timed_out.status == "error"
    assert results["m"].errorCode == Codes.NGSPICE_TIMEOUT
    assert results["m"].value is None
    assert all(result.value is None for result in timed_out.results)
    assert any(warning.code == Codes.NGSPICE_TIMEOUT for warning in timed_out.warnings)

    if not HAS_NGSPICE:  # pragma: no cover - environment dependent
        pytest.skip("recovery half needs a real ngspice")

    # same graph, same engine, healthy binary: recovers with no residue from the failed run
    recovered = run(graph_dict, settings, revision=2)
    recovered_results = by_id(recovered)
    assert recovered.status == "ok"
    assert recovered_results["m"].value == pytest.approx(64.0)
    assert recovered_results["screen"].value == pytest.approx(64.0)


def test_service_stays_healthy_after_a_timeout(settings, tmp_path, monkeypatch) -> None:
    slow = replace(settings, ngspice_bin=_fake_binary(tmp_path, "sleep 30"), timeout_seconds=0.5, log_netlists=False)
    monkeypatch.setattr(main_module, "settings", slow)

    graph_dict = graph(
        "timeout via http",
        [node("n1", "number", {"value": 1}), node("screen", "display")],
        [edge("e1", "n1", "screen", "out", "in")],
    )
    payload = client.post("/simulate", json={"graph": graph_dict, "revision": 1}).json()
    assert payload["status"] == "error"
    assert all(result["value"] is None for result in payload["results"])

    # /health must still answer (degraded only because the binary is a stub that never exits)
    health = client.get("/health")
    assert health.status_code in (200, 503)
    assert "ngspice" in health.json()


def test_unexpected_crash_is_reported_as_a_structured_error(settings, tmp_path) -> None:
    crashing = replace(settings, ngspice_bin=_fake_binary(tmp_path, 'echo "boom" >&2\nexit 9'))
    graph_dict = graph(
        "crash",
        [node("n1", "number", {"value": 3}), node("screen", "display")],
        [edge("e1", "n1", "screen", "out", "in")],
    )
    response = run(graph_dict, crashing)
    results = by_id(response)

    assert response.status == "error"
    assert results["n1"].status == "error"
    assert results["n1"].errorCode in (Codes.NGSPICE_ERROR, Codes.PARSE_ERROR)
    assert results["n1"].value is None
    assert results["screen"].status == "blocked"
    assert all(result.value is None for result in response.results)
