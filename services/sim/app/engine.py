"""
Simulation engine — orchestration only.

Order of work (each step lives in its own module):

    1. analyze_graph()      validation, connectivity, cycle detection      -> graph.py
    2. parse()              strict formula parsing (never eval/exec)       -> formula.py
    3. static folding       constant propagation + pre-simulation div/0    -> this module
    4. build_netlist()      deck generation ending in .op/.end            -> netlist.py
    5. run_batch()          `ngspice -b` with timeout & kill              -> executor.py
    6. parse_op_output()    node-voltage table -> values                  -> parser.py
    7. assemble()           per-node statuses, blocked propagation, codes -> this module

Guarantees:
* a node that failed never contributes a stale value: every value in the response comes from this run
* any node downstream of an error/cycle is `blocked`, never silently zero
* nothing reaches ngspice unless validation, connectivity, cycle detection and formula parsing passed
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Iterable

from . import executor, netlist, parser
from .config import Settings, get_settings
from .errors import (
    NGSPICE_CLAMP_MAGNITUDE,
    Codes,
    FormulaError,
    NetlistError,
    NgspiceNotFound,
    OutputParseError,
    SimulationError,
)
from .formula import Ast, evaluate, identifiers, parse
from .graph import GraphAnalysis, analyze_graph, spec_for
from .schemas import GraphSpec, GraphWarning, NodeIssue, NodeResult, SimulateResponse, Timings

logger = logging.getLogger("lng.sim.engine")

RELATIVE_TOLERANCE = 1e-6
ZERO_DENOMINATOR_EPS = 1e-12
MESSAGE_LIMIT = 600


@dataclass
class _NodeState:
    status: str = "ok"  # ok | error | blocked
    value: float | None = None
    source: str | None = None
    net: str | None = None
    error_code: str | None = None
    message: str | None = None
    issues: list[NodeIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    input_states: dict[str, str] = field(default_factory=dict)


def evaluate_graph(
    graph: GraphSpec,
    *,
    revision: int = 0,
    settings: Settings | None = None,
) -> SimulateResponse:
    settings = settings or get_settings()
    started = time.perf_counter()

    # ---- 1. validation & topology -------------------------------------------------------------------
    validation_started = time.perf_counter()
    analysis = analyze_graph(graph, max_nodes=settings.max_nodes, max_edges=settings.max_edges)
    states: dict[str, _NodeState] = {
        node_id: _NodeState(issues=[issue.model_copy() for issue in analysis.issues_for(node_id)])
        for node_id in analysis.node_by_id
    }
    for node_id, state in states.items():
        if any(issue.severity == "error" for issue in state.issues):
            state.status = "error"
            state.error_code = state.issues[0].code
            state.message = state.issues[0].message

    # ---- 2. formula parsing (strict, no eval/exec) ---------------------------------------------------
    asts: dict[str, Ast] = {}
    for node_id in analysis.order:
        node = analysis.node_by_id[node_id]
        if node.type != "formula" or states[node_id].status == "error":
            continue
        expression = node.data.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            _fail(
                states[node_id],
                Codes.GRAPH_SCHEMA_INVALID,
                f'node "{node_id}" has no formula expression',
                analysis,
            )
            continue
        try:
            ast = parse(expression)
        except FormulaError as error:
            _fail(states[node_id], error.code, f'node "{node_id}": {error.message}', analysis)
            continue
        asts[node_id] = ast
        for ident in sorted(identifiers(ast)):
            if (node_id, ident) not in analysis.connections:
                _fail(
                    states[node_id],
                    Codes.MISSING_INPUT,
                    f'node "{node_id}" expression uses "{ident}" but input "{ident}" is not wired',
                    analysis,
                    handle=ident,
                )

    # ---- 3. static folding: literals, constant arithmetic, pre-simulation divide-by-zero -------------
    static: dict[str, float] = {}
    literals: dict[str, float] = {}
    for node_id in analysis.order:
        node = analysis.node_by_id[node_id]
        state = states[node_id]
        if state.status == "error":
            continue

        if node.type == "number":
            value = float(node.data.get("value"))  # type: ignore[arg-type]
            literals[node_id] = value
            static[node_id] = value
            continue

        input_static = {
            handle: static.get(connection.source_id)
            for (target, handle), connection in analysis.connections.items()
            if target == node_id
        }

        if node.type == "divide" and input_static.get("b") == 0.0:
            _fail(
                states[node_id],
                Codes.DIVIDE_BY_ZERO,
                f'node "{node_id}": denominator (input b) is 0',
                analysis,
                handle="b",
            )
            continue

        if node.type in ("add", "multiply", "divide"):
            left = input_static.get("a")
            right = input_static.get("b")
            if left is None or right is None:
                continue
            if node.type == "divide" and abs(right) < ZERO_DENOMINATOR_EPS:
                _fail(
                    states[node_id],
                    Codes.DIVIDE_BY_ZERO,
                    f'node "{node_id}": denominator (input b) is {right:g}, which is zero within tolerance',
                    analysis,
                    handle="b",
                )
                continue
            if node.type == "add":
                static[node_id] = left + right
            elif node.type == "multiply":
                static[node_id] = left * right
            else:  # divide, denominator already proven non-zero above
                static[node_id] = left / right
            continue

        if node.type == "formula":
            ast = asts.get(node_id)
            if ast is None:
                continue
            bound = {}
            complete = True
            for ident in identifiers(ast):
                value = input_static.get(ident)
                if value is None:
                    complete = False
                    break
                bound[ident] = value
            if not complete:
                continue
            try:
                static[node_id] = evaluate(ast, bound)
            except FormulaError as error:
                _fail(states[node_id], error.code, f'node "{node_id}": {error.message}', analysis)

    # ---- 4. blocked propagation (errors + cycles reach every descendant) ----------------------------
    blocked = _propagate_blocked(analysis, states)
    for node_id in blocked:
        state = states[node_id]
        if state.status == "error":
            continue
        upstream = _first_failed_upstream(analysis, states, node_id)
        state.status = "blocked"
        state.error_code = Codes.UPSTREAM_ERROR
        state.message = (
            f'node "{node_id}" is blocked because upstream node "{upstream}" has an error'
            if upstream
            else f'node "{node_id}" is blocked by an upstream cycle'
        )
        state.issues.append(NodeIssue(code=Codes.UPSTREAM_ERROR, message=state.message, severity="error"))

    validation_ms = (time.perf_counter() - validation_started) * 1000.0

    # ---- 5. netlist ---------------------------------------------------------------------------------
    netlist_started = time.perf_counter()
    evaluable = [
        node_id
        for node_id in analysis.order
        if states[node_id].status == "ok" and node_id not in analysis.cyclic and node_id not in blocked
    ]
    plan = netlist.NetlistPlan(text="")
    try:
        plan = netlist.build_netlist(
            graph,
            analysis,
            revision=revision,
            evaluable=evaluable,
            asts=asts,
            literals=literals,
        )
    except (NetlistError, SimulationError) as error:
        for node_id in evaluable:
            _fail(states[node_id], getattr(error, "code", Codes.NETLIST_ERROR), str(error), analysis)
        plan.text = f"* netlist generation failed: {error}"
    netlist_ms = (time.perf_counter() - netlist_started) * 1000.0

    # ---- 6. execution -------------------------------------------------------------------------------
    ngspice_ms = 0.0
    warnings: list[GraphWarning] = [
        GraphWarning(code=issue.code, message=issue.message) for issue in analysis.graph_issues
    ]
    run_error_code: str | None = None
    run_error_message: str | None = None
    ngspice_ms_measured = 0.0

    if plan.device_count == 0:
        logger.info("nothing to simulate (revision=%s, nodes=%d)", revision, len(graph.nodes))
    else:
        try:
            run = executor.run_batch(plan.text, settings, label=f"revision-{revision}")
        except NgspiceNotFound as error:
            run = None
            run_error_code = Codes.NGSPICE_MISSING
            run_error_message = error.message
        else:
            ngspice_ms_measured = run.duration_ms
            if not run.ok:
                run_error_code = run.error_code or Codes.NGSPICE_ERROR
                run_error_message = run.message or "ngspice failed"
                warnings.append(
                    GraphWarning(
                        code=run_error_code,
                        message=_truncate(
                            f"{run_error_message}. ngspice log tail: "
                            f"{_log_excerpt(run.log, run.timed_out)}",
                        ),
                    )
                )
            else:
                parse_started = time.perf_counter()
                try:
                    point, mismatch_warnings, used_raw = parser.parse_run_output(
                        run.log,
                        run.raw,
                        expect_nets=list(plan.net_by_node.values()),
                        truncated=run.log_truncated,
                    )
                    for message in mismatch_warnings:
                        warnings.append(GraphWarning(code="NGSPICE_VALUE_MISMATCH", message=_truncate(message)))
                    if not used_raw:
                        warnings.append(
                            GraphWarning(
                                code="NGSPICE_RAW_UNAVAILABLE",
                                message="ngspice produced no ASCII raw file; values come from the 6-digit .op table",
                            )
                        )
                    _apply_simulation(analysis, states, plan, point)
                    for warning in point.warnings[:10]:
                        warnings.append(GraphWarning(code="NGSPICE_WARNING", message=_truncate(warning)))
                    if run.log_truncated:
                        warnings.append(
                            GraphWarning(
                                code="NGSPICE_LOG_TRUNCATED",
                                message=f"ngspice log exceeded {settings.max_output_bytes} bytes; only the tail was parsed",
                            )
                        )
                except OutputParseError as error:
                    run_error_code = error.code
                    run_error_message = error.message
                    warnings.append(
                        GraphWarning(code=error.code, message=_truncate(f"{error.message}. {error.detail or ''}"))
                    )
                ngspice_ms = (time.perf_counter() - parse_started) * 1000.0

        if run_error_code is not None:
            _handle_run_failure(states, plan, run_error_code, run_error_message or "ngspice failed")

    # ---- 7. assemble --------------------------------------------------------------------------------
    results = [_to_result(node_id, states[node_id], analysis, states) for node_id in _result_order(analysis)]
    status = _overall_status(results)
    total_ms = (time.perf_counter() - started) * 1000.0

    logger.info(
        "simulate revision=%s status=%s nodes=%d devices=%d errors=%d blocked=%d ngspice_ms=%.1f total_ms=%.1f",
        revision,
        status,
        len(results),
        plan.device_count,
        sum(1 for result in results if result.status == "error"),
        sum(1 for result in results if result.status == "blocked"),
        ngspice_ms_measured,
        total_ms,
    )

    return SimulateResponse(
        revision=revision,
        status=status,
        results=results,
        warnings=warnings,
        netlist=plan.text or None,
        netlistTruncated=False,
        ngspiceVersion=_cached_version(settings),
        timings=Timings(
            validationMs=round(validation_ms, 3),
            netlistMs=round(netlist_ms, 3),
            ngspiceMs=round(ngspice_ms_measured, 3),
            parseMs=round(ngspice_ms, 3),
            totalMs=round(total_ms, 3),
        ),
        topology={"order": analysis.order, "cyclic": analysis.cyclic, "blocked": analysis.blocked},
    )


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------


def _apply_simulation(analysis: GraphAnalysis, states: dict[str, _NodeState], plan: netlist.NetlistPlan, point: parser.OpPoint) -> None:
    """Assign simulated values and run post-simulation checks in evaluation order."""
    for node_id in plan.simulated_nodes:
        state = states[node_id]
        if state.status != "ok":
            continue
        node = analysis.node_by_id[node_id]
        net = plan.net_by_node[node_id]
        value = point.voltages.get(net)
        if value is None:
            _fail(states[node_id], Codes.PARSE_ERROR, f'node "{node_id}": net "{net}" missing from ngspice output', analysis)
            continue
        if value != value or value in (float("inf"), float("-inf")):
            _fail(
                states[node_id],
                Codes.NON_NUMERIC_VALUE,
                f'node "{node_id}": ngspice produced a non-finite value ({value})',
                analysis,
            )
            continue

        input_values: dict[str, float | None] = {}
        for (target, handle), connection in analysis.connections.items():
            if target != node_id:
                continue
            source_net = plan.net_by_node.get(connection.source_id)
            input_values[handle] = point.voltages.get(source_net) if source_net else None

        if node.type == "divide":
            denominator = input_values.get("b")
            if denominator is not None and abs(denominator) < ZERO_DENOMINATOR_EPS:
                _fail(
                    states[node_id],
                    Codes.DIVIDE_BY_ZERO,
                    f'node "{node_id}": division by zero (denominator = {denominator:g})',
                    analysis,
                    handle="b",
                )
                continue
            if abs(value) >= NGSPICE_CLAMP_MAGNITUDE:
                _fail(
                    states[node_id],
                    Codes.DIVIDE_BY_ZERO,
                    f'node "{node_id}": result overflowed the ngspice division clamp ({value:g})',
                    analysis,
                    handle="b",
                )
                continue

        if node.type == "formula":
            ast = None
            expression = node.data.get("expression")
            if isinstance(expression, str):
                try:
                    ast = parse(expression)
                except FormulaError:  # already reported during validation
                    ast = None
            if ast is not None:
                needed = identifiers(ast)
                missing_values = [ident for ident in sorted(needed) if input_values.get(ident) is None]
                if missing_values:
                    _fail(
                        states[node_id],
                        Codes.UPSTREAM_ERROR,
                        f'node "{node_id}": input(s) {", ".join(missing_values)} produced no value',
                        analysis,
                        handle=missing_values[0],
                    )
                    continue
                bound = {ident: float(input_values[ident]) for ident in needed}  # type: ignore[arg-type]
                try:
                    expected = evaluate(ast, bound)
                except FormulaError as error:
                    _fail(states[node_id], error.code, f'node "{node_id}": {error.message}', analysis)
                    continue
                if not _close_enough(value, expected):
                    state.warnings.append(
                        f"netlist value {value:g} differs from the expression evaluation {expected:g} "
                        "(ngspice numerical tolerance)"
                    )

        if node.type == "number":
            literal = float(node.data.get("value"))  # type: ignore[arg-type]
            if not _close_enough(value, literal):
                state.warnings.append(f"ngspice returned {value:g} for literal {literal:g}")

        state.value = value
        state.source = "simulation"
        state.net = net

    for node_id in plan.probe_only_nodes:
        state = states[node_id]
        if state.status != "ok":
            continue
        net = plan.upstream_net[node_id]
        value = point.voltages.get(net)
        if value is None:
            _fail(states[node_id], Codes.PARSE_ERROR, f'node "{node_id}": upstream net "{net}" missing from output', analysis)
            continue
        state.value = value
        state.source = "simulation"
        state.net = net


def _handle_run_failure(states: dict[str, _NodeState], plan: netlist.NetlistPlan, code: str, message: str) -> None:
    """ngspice crashed / timed out / is unavailable: no node keeps a value."""
    affected = [*plan.simulated_nodes, *plan.probe_only_nodes]
    for node_id in affected:
        state = states[node_id]
        if state.status != "ok":
            continue
        state.value = None
        state.source = None
        if node_id in plan.probe_only_nodes:
            state.status = "blocked"
            state.error_code = Codes.UPSTREAM_ERROR
            state.message = f'node "{node_id}" is blocked: the simulation run failed'
        else:
            state.status = "error"
            state.error_code = code
            state.message = _truncate(message)
        state.issues.append(NodeIssue(code=state.error_code or code, message=state.message or message, severity="error"))


def _propagate_blocked(analysis: GraphAnalysis, states: dict[str, _NodeState]) -> set[str]:
    accepted_out: dict[str, list[str]] = {node_id: [] for node_id in analysis.node_by_id}
    for connection in analysis.connections.values():
        accepted_out.setdefault(connection.source_id, []).append(connection.target_id)

    blocked: set[str] = {node_id for node_id in analysis.blocked}
    queue = [node_id for node_id, state in states.items() if state.status == "error"]
    queue.extend(analysis.cyclic)
    seen = set(queue)
    while queue:
        current = queue.pop(0)
        for child in accepted_out.get(current, []):
            if child in seen:
                continue
            seen.add(child)
            blocked.add(child)
            queue.append(child)
    return blocked


def _first_failed_upstream(analysis: GraphAnalysis, states: dict[str, _NodeState], node_id: str) -> str | None:
    for (target, _handle), connection in analysis.connections.items():
        if target != node_id:
            continue
        source_state = states.get(connection.source_id)
        if source_state is not None and source_state.status in ("error", "blocked"):
            return connection.source_id
    return None


def _input_state(analysis: GraphAnalysis, states: dict[str, _NodeState], node_id: str, handle: str) -> str:
    connection = analysis.connections.get((node_id, handle))
    if connection is None:
        return "missing"
    upstream = states.get(connection.source_id)
    if upstream is None:
        return "error"
    if upstream.status == "error":
        return "error"
    if upstream.status == "blocked":
        return "blocked"
    if upstream.value == 0.0:
        return "zero"
    return "ok"


def _input_handles(node_type: str) -> tuple[str, ...]:
    spec = spec_for(node_type)
    return spec.inputs if spec else ()


def _fail(state: _NodeState, code: str, message: str, analysis: GraphAnalysis, *, handle: str | None = None) -> None:
    state.status = "error"
    state.error_code = code
    state.message = _truncate(message)
    state.value = None
    state.issues.append(NodeIssue(code=code, message=_truncate(message), severity="error", handle=handle))


def _to_result(node_id: str, state: _NodeState, analysis: GraphAnalysis, states: dict[str, _NodeState]) -> NodeResult:
    node = analysis.node_by_id.get(node_id)
    # Computed here (not during simulation) so blocked and errored nodes also report per-input
    # states: the UI can then show *which* upstream input caused the block.
    input_states = {
        handle: _input_state(analysis, states, node_id, handle)
        for handle in _input_handles(node.type or "")
        if states.get(node_id) is not None
    }
    return NodeResult(
        nodeId=node_id,
        status=state.status,  # type: ignore[arg-type]
        value=state.value,
        errorCode=state.error_code,
        message=state.message,
        source=state.source,  # type: ignore[arg-type]
        net=state.net,
        issues=state.issues,
        warnings=state.warnings,
        inputStates=input_states,
    )


def _result_order(analysis: GraphAnalysis) -> list[str]:
    remaining = [node_id for node_id in analysis.node_by_id if node_id not in analysis.order]
    return [*analysis.order, *sorted(remaining)]


def _overall_status(results: Iterable[NodeResult]) -> str:
    results = list(results)
    if not results:
        return "ok"
    errors = sum(1 for result in results if result.status == "error")
    blocked = sum(1 for result in results if result.status == "blocked")
    values = sum(1 for result in results if result.status == "ok")
    if errors == 0 and blocked == 0:
        return "ok"
    if values == 0:
        return "error"
    return "partial"


def _close_enough(left: float, right: float, tolerance: float = RELATIVE_TOLERANCE) -> bool:
    return abs(left - right) <= tolerance * max(1.0, abs(left), abs(right))


def _truncate(message: str, limit: int = MESSAGE_LIMIT) -> str:
    message = " ".join(str(message).split())
    return message if len(message) <= limit else f"{message[:limit]}…"


def _log_excerpt(log: str, timed_out: bool) -> str:
    lines = [line.strip() for line in log.splitlines() if line.strip()]
    tail = lines[-6:]
    if timed_out:
        tail.append("(process group was killed after the timeout expired)")
    return " | ".join(tail) if tail else "(empty log)"


_version_cache: dict[str, tuple[str | None, str | None]] = {}


def _cached_version(settings: Settings) -> str | None:
    key = settings.ngspice_bin
    if key not in _version_cache:
        _version_cache[key] = executor.ngspice_version(settings)
    return _version_cache[key][0]


def ngspice_health(settings: Settings | None = None) -> tuple[str | None, str | None]:
    """Version string + error message, cached per binary path (used by /health)."""
    settings = settings or get_settings()
    key = settings.ngspice_bin
    if key not in _version_cache:
        _version_cache[key] = executor.ngspice_version(settings)
    return _version_cache[key]
