"""
Pydantic contracts for the simulation service.

Mirror of packages/graph-contract/graph.schema.json — the cross-language contract test
(services/sim/tests/test_contract_mirror.py) fails if the JSON Schema and these models disagree.
Extra fields coming from React Flow (`selected`, `width`, `measured`, ...) are preserved so that a
graph can be saved back verbatim by the API.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

NodeTypeName = Literal["number", "add", "multiply", "divide", "formula", "display"]
Severity = Literal["error", "warning"]
NodeStatus = Literal["ok", "error", "blocked"]
RunStatus = Literal["ok", "partial", "error"]


class Position(BaseModel):
    model_config = ConfigDict(extra="allow")
    x: float
    y: float


class NodeData(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: float | None = None
    expression: str | None = None


class Node(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(min_length=1, max_length=64)
    type: str  # validated against the registry (unknown types produce UNKNOWN_NODE_TYPE issues)
    position: Position
    data: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(min_length=1, max_length=64)
    source: str
    target: str
    sourceHandle: str
    targetHandle: str


class GraphSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str | None = Field(default=None, max_length=120)
    nodes: list[Node] = Field(max_length=500)
    edges: list[Edge] = Field(max_length=1000)


class NodeIssue(BaseModel):
    code: str
    message: str
    severity: Severity = "error"
    handle: str | None = None


class NodeResult(BaseModel):
    nodeId: str
    status: NodeStatus
    value: float | None = None
    errorCode: str | None = None
    message: str | None = None
    source: Literal["literal", "simulation", "none"] | None = None
    net: str | None = None
    issues: list[NodeIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    inputStates: dict[str, str] = Field(default_factory=dict)


class GraphWarning(BaseModel):
    code: str
    message: str
    nodeIds: list[str] = Field(default_factory=list)
    edgeIds: list[str] = Field(default_factory=list)


class Timings(BaseModel):
    validationMs: float = 0.0
    netlistMs: float = 0.0
    ngspiceMs: float = 0.0
    parseMs: float = 0.0
    totalMs: float = 0.0


class SimulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    graph: GraphSpec
    revision: int = Field(default=0, ge=0)


class SimulateResponse(BaseModel):
    revision: int
    status: RunStatus
    results: list[NodeResult] = Field(default_factory=list)
    warnings: list[GraphWarning] = Field(default_factory=list)
    netlist: str | None = None
    netlistTruncated: bool = False
    ngspiceVersion: str | None = None
    timings: Timings = Field(default_factory=Timings)
    topology: dict[str, list[str]] = Field(default_factory=dict)
    message: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str = "simulation"
    ngspice: dict[str, Any]
    config: dict[str, Any]
    uptimeSeconds: float


class ErrorBody(BaseModel):
    code: str
    message: str
    nodeIds: list[str] = Field(default_factory=list)
    edgeIds: list[str] = Field(default_factory=list)
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
