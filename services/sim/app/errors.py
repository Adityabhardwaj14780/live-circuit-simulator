"""Stable error codes shared with the TypeScript contract (packages/graph-contract/graph.schema.json)."""

from __future__ import annotations


class Codes:
    GRAPH_SCHEMA_INVALID = "GRAPH_SCHEMA_INVALID"
    UNKNOWN_NODE_TYPE = "UNKNOWN_NODE_TYPE"
    UNKNOWN_HANDLE = "UNKNOWN_HANDLE"
    SELF_LOOP = "SELF_LOOP"
    DUPLICATE_INPUT_EDGE = "DUPLICATE_INPUT_EDGE"
    MISSING_INPUT = "MISSING_INPUT"
    CYCLE_DETECTED = "CYCLE_DETECTED"
    NON_NUMERIC_VALUE = "NON_NUMERIC_VALUE"
    FORMULA_SYNTAX_ERROR = "FORMULA_SYNTAX_ERROR"
    FORMULA_DISALLOWED_TOKEN = "FORMULA_DISALLOWED_TOKEN"
    DIVIDE_BY_ZERO = "DIVIDE_BY_ZERO"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    NETLIST_ERROR = "NETLIST_ERROR"
    NGSPICE_ERROR = "NGSPICE_ERROR"
    NGSPICE_TIMEOUT = "NGSPICE_TIMEOUT"
    NGSPICE_MISSING = "NGSPICE_MISSING"
    PARSE_ERROR = "PARSE_ERROR"
    SIMULATION_SERVICE_UNAVAILABLE = "SIMULATION_SERVICE_UNAVAILABLE"
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    VALIDATION_FAILED = "VALIDATION_FAILED"


HIERARCHICAL_CHILD_CODES = {
    Codes.UPSTREAM_ERROR,
}

# Magnitude at which ngspice clamps a behavioural-source division by zero (verified with ngspice 44.2).
NGSPICE_CLAMP_MAGNITUDE = 1.0e32
# Denominators below this magnitude are treated as effectively zero rather than "very small".
ZERO_DENOMINATOR_EPS = 1.0e-12


class SimulationError(Exception):
    """Base class for failures that map onto an error code."""

    code = Codes.NGSPICE_ERROR

    def __init__(self, message: str, code: str | None = None, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.detail = detail


class NgspiceNotFound(SimulationError):
    code = Codes.NGSPICE_MISSING


class NgspiceTimeout(SimulationError):
    code = Codes.NGSPICE_TIMEOUT


class NgspiceFailed(SimulationError):
    code = Codes.NGSPICE_ERROR


class NetlistError(SimulationError):
    code = Codes.NETLIST_ERROR


class OutputParseError(SimulationError):
    code = Codes.PARSE_ERROR


class FormulaError(ValueError):
    """Raised by the strict formula tokenizer/parser/evaluator."""

    def __init__(self, message: str, code: str = Codes.FORMULA_SYNTAX_ERROR, position: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.position = position
