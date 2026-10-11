"""
The eight acceptance scenarios, expressed as data (never hard-coded into the engine).

Each entry declares the graph, the expected per-node values, statuses and error codes, so the same
definition drives graph execution, validation, loop detection and Formula-safety tests. The runner
is tests/test_acceptance_graphs.py; tests/e2e/acceptance-http.mjs replays the same data against a
live stack over HTTP so the results are also verified through the real API boundary.

    1. s1_number_display  Number -> Display                                  (basic execution)
    2. s2_add              2 + 3 = 5                                         (add mapping)
    3. s3_multiply         6 * 7 = 42                                        (multiply mapping)
    4. s4_divide           22 / 7                                            (divide mapping, full precision)
    5. s5_fan_out          one output feeding two consumers                  (fan-out rule)
    6. s6_missing_input    Add with input "b" unwired                        (MISSING_INPUT + blocked)
    7. s7_cycle            add -> multiply -> add feedback loop              (CYCLE_DETECTED + blocked)
    8. s8_formula_safety   correct formula, division by zero and a code-injection payload
                           in one graph                                      (ok / DIVIDE_BY_ZERO /
                                                                              FORMULA_DISALLOWED_TOKEN)
"""

from __future__ import annotations

from typing import Any

GraphDict = dict[str, Any]


def node(node_id: str, node_type: str, data: dict[str, Any] | None = None, x: int = 0, y: int = 0) -> GraphDict:
    return {"id": node_id, "type": node_type, "position": {"x": x, "y": y}, "data": data or {}}


def edge(edge_id: str, source: str, target: str, source_handle: str, target_handle: str) -> GraphDict:
    return {
        "id": edge_id,
        "source": source,
        "sourceHandle": source_handle,
        "target": target,
        "targetHandle": target_handle,
    }


def graph(name: str, nodes: list[GraphDict], edges: list[GraphDict]) -> GraphDict:
    return {"name": name, "nodes": nodes, "edges": edges}


ACCEPTANCE_GRAPHS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------ 1
    {
        "id": "s1_number_display",
        "title": "Number -> Display",
        "covers": ["basic execution", "number mapping", "display probe"],
        "expectStatus": "ok",
        "expectValues": {"n1": 42.0, "d1": 42.0},
        "expectValueTolerance": 1e-9,
        "graph": graph(
            "s1 number->display",
            [node("n1", "number", {"value": 42}), node("d1", "display", x=240)],
            [edge("e1", "n1", "d1", "out", "in")],
        ),
    },
    # ------------------------------------------------------------------ 2
    {
        "id": "s2_add",
        "title": "2 + 3 = 5",
        "covers": ["add mapping"],
        "expectStatus": "ok",
        "expectValues": {"n1": 2.0, "n2": 3.0, "add1": 5.0, "d1": 5.0},
        "expectValueTolerance": 1e-9,
        "expectNetlistContains": ["+ V(n2)"],
        "graph": graph(
            "s2 add",
            [node("n1", "number", {"value": 2}), node("n2", "number", {"value": 3}, y=120), node("add1", "add", x=240), node("d1", "display", x=480)],
            [
                edge("e1", "n1", "add1", "out", "a"),
                edge("e2", "n2", "add1", "out", "b"),
                edge("e3", "add1", "d1", "out", "in"),
            ],
        ),
    },
    # ------------------------------------------------------------------ 3
    {
        "id": "s3_multiply",
        "title": "6 * 7 = 42",
        "covers": ["multiply mapping"],
        "expectStatus": "ok",
        "expectValues": {"n1": 6.0, "n2": 7.0, "mul1": 42.0, "d1": 42.0},
        "expectValueTolerance": 1e-9,
        "expectNetlistContains": ["* V(n2)"],
        "graph": graph(
            "s3 multiply",
            [node("n1", "number", {"value": 6}), node("n2", "number", {"value": 7}, y=120), node("mul1", "multiply", x=240), node("d1", "display", x=480)],
            [
                edge("e1", "n1", "mul1", "out", "a"),
                edge("e2", "n2", "mul1", "out", "b"),
                edge("e3", "mul1", "d1", "out", "in"),
            ],
        ),
    },
    # ------------------------------------------------------------------ 4
    {
        "id": "s4_divide",
        "title": "22 / 7 = 3.142857142857143",
        "covers": ["divide mapping", "full-precision output parsing"],
        "expectStatus": "ok",
        "expectValues": {"n1": 22.0, "n2": 7.0, "div1": 22.0 / 7.0, "d1": 22.0 / 7.0},
        "expectValueTolerance": 1e-12,
        "expectNetlistContains": ["/ V(n2)"],
        "graph": graph(
            "s4 divide",
            [node("n1", "number", {"value": 22}), node("n2", "number", {"value": 7}, y=120), node("div1", "divide", x=240), node("d1", "display", x=480)],
            [
                edge("e1", "n1", "div1", "out", "a"),
                edge("e2", "n2", "div1", "out", "b"),
                edge("e3", "div1", "d1", "out", "in"),
            ],
        ),
    },
    # ------------------------------------------------------------------ 5
    {
        "id": "s5_fan_out",
        "title": "one output feeds two consumers",
        "covers": ["fan-out rule (one output -> many inputs)"],
        "expectStatus": "ok",
        "expectValues": {"n1": 5.0, "add1": 10.0, "mul1": 25.0, "d1": 10.0, "d2": 25.0},
        "expectValueTolerance": 1e-9,
        "graph": graph(
            "s5 fan-out",
            [
                node("n1", "number", {"value": 5}),
                node("add1", "add", x=240, y=0),
                node("mul1", "multiply", x=240, y=170),
                node("d1", "display", x=480, y=0),
                node("d2", "display", x=480, y=170),
            ],
            [
                edge("e1", "n1", "add1", "out", "a"),
                edge("e2", "n1", "add1", "out", "b"),
                edge("e3", "n1", "mul1", "out", "a"),
                edge("e4", "n1", "mul1", "out", "b"),
                edge("e5", "add1", "d1", "out", "in"),
                edge("e6", "mul1", "d2", "out", "in"),
            ],
        ),
    },
    # ------------------------------------------------------------------ 6
    {
        "id": "s6_missing_input",
        "title": "Add with input b unwired: node-specific error, downstream blocked",
        "covers": ["missing input detection", "node-specific error", "blocked downstream state"],
        "expectStatus": "partial",
        "expectValues": {"n1": 7.0},
        "expectValueTolerance": 1e-9,
        "expectErrorCodes": {"add1": "MISSING_INPUT", "d1": "UPSTREAM_ERROR"},
        "expectStatuses": {"n1": "ok", "add1": "error", "d1": "blocked"},
        "expectIssueHandles": {"add1": ["b"]},
        # only the healthy number node becomes a device; the unwired Add never reaches the deck
        "expectDeviceLines": ["V1 n1 0 DC 7"],
        "graph": graph(
            "s6 missing input",
            [node("n1", "number", {"value": 7}), node("add1", "add", x=240), node("d1", "display", x=480)],
            [
                edge("e1", "n1", "add1", "out", "a"),
                edge("e2", "add1", "d1", "out", "in"),
            ],
        ),
    },
    # ------------------------------------------------------------------ 7
    {
        "id": "s7_cycle",
        "title": "feedback loop add -> multiply -> add with a display downstream",
        "covers": ["cycle detection", "blocked descendants of a cycle", "healthy sibling still evaluates"],
        "expectStatus": "partial",
        "expectValues": {"src": 1.0},
        "expectValueTolerance": 1e-9,
        "expectErrorCodes": {
            "cyc_add": "CYCLE_DETECTED",
            "cyc_mul": "CYCLE_DETECTED",
            "d1": "UPSTREAM_ERROR",
        },
        "expectStatuses": {"src": "ok", "cyc_add": "error", "cyc_mul": "error", "d1": "blocked"},
        "expectCyclic": ["cyc_add", "cyc_mul"],
        "expectBlocked": ["d1"],
        # the feedback loop contributes no device at all: ngspice only ever sees the healthy source
        "expectDeviceLines": ["V1 n1 0 DC 1"],
        "graph": graph(
            "s7 cycle",
            [
                node("src", "number", {"value": 1}),
                node("cyc_add", "add", x=240),
                node("cyc_mul", "multiply", x=480),
                node("d1", "display", x=720),
            ],
            [
                edge("e1", "src", "cyc_add", "out", "a"),
                edge("e2", "cyc_mul", "cyc_add", "out", "b"),
                edge("e3", "cyc_add", "cyc_mul", "out", "a"),
                edge("e4", "src", "cyc_mul", "out", "b"),
                edge("e5", "cyc_mul", "d1", "out", "in"),
            ],
        ),
    },
    # ------------------------------------------------------------------ 8
    {
        "id": "s8_formula_safety",
        "title": "formula correctness, division by zero and a code-injection payload in one graph",
        "covers": [
            "formula correctness",
            "malicious formula rejection",
            "division by zero",
            "healthy subgraph still simulates",
        ],
        "expectStatus": "partial",
        "expectValues": {
            "n1": 4.0,
            "n2": 6.0,
            "good_formula": 25.0,
            "d_ok": 25.0,
            "n3": 10.0,
            "n4": 0.0,
            "add_ok": 10.0,
        },
        "expectValueTolerance": 1e-9,
        "expectErrorCodes": {
            "div0": "DIVIDE_BY_ZERO",
            "bad_formula": "FORMULA_DISALLOWED_TOKEN",
            "d_div": "UPSTREAM_ERROR",
            "d_bad": "UPSTREAM_ERROR",
        },
        "expectStatuses": {
            "good_formula": "ok",
            "d_ok": "ok",
            "add_ok": "ok",
            "div0": "error",
            "bad_formula": "error",
            "d_div": "blocked",
            "d_bad": "blocked",
        },
        # the injected payload must not survive into the deck, in any form (comments included)
        "expectNetlistExcludes": ["__import__", "system", "id("],
        "graph": graph(
            "s8 formula safety",
            [
                node("n1", "number", {"value": 4}),
                node("n2", "number", {"value": 6}, y=110),
                node("good_formula", "formula", {"expression": "a * b + 1"}, x=240),
                node("d_ok", "display", x=480),
                node("n3", "number", {"value": 10}, y=280),
                node("n4", "number", {"value": 0}, y=380),
                node("div0", "divide", x=240, y=280),
                node("add_ok", "add", x=240, y=400),
                node("bad_formula", "formula", {"expression": "__import__('os').system('id')"}, x=240, y=520),
                node("d_div", "display", x=480, y=280),
                node("d_bad", "display", x=480, y=520),
            ],
            [
                edge("e1", "n1", "good_formula", "out", "a"),
                edge("e2", "n2", "good_formula", "out", "b"),
                edge("e3", "good_formula", "d_ok", "out", "in"),
                edge("e4", "n3", "div0", "out", "a"),
                edge("e5", "n4", "div0", "out", "b"),
                edge("e6", "n3", "add_ok", "out", "a"),
                edge("e7", "n4", "add_ok", "out", "b"),
                edge("e8", "n3", "bad_formula", "out", "a"),
                edge("e9", "n4", "bad_formula", "out", "b"),
                edge("e10", "div0", "d_div", "out", "in"),
                edge("e11", "bad_formula", "d_bad", "out", "in"),
            ],
        ),
    },
]

ACCEPTANCE_BY_ID = {entry["id"]: entry for entry in ACCEPTANCE_GRAPHS}
