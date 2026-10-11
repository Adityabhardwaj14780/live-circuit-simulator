/**
 * Structural validation & normalisation for incoming graphs.
 *
 * The authoritative simulation-time validation lives in Python (services/sim/app/graph.py); this
 * module gives the client immediate feedback and guarantees the API never persists something that is
 * not a graph at all. Rejected edges are reported with their ids rather than silently dropped.
 */
import {
  LIMITS,
  analyzeTopology,
  isGraphShape,
  validateGraphStructure,
  type Graph,
  type GraphEdge,
  type GraphNode,
  type Issue,
} from '@lng/graph-contract';
import { ApiError, ErrorCodes } from './errors.js';

export interface ValidationReport {
  ok: boolean;
  issues: Issue[];
  topology: { order: string[]; cyclic: string[]; blocked: string[] };
  counts: { nodes: number; edges: number };
}

/** Coerce unknown JSON into a Graph shape, keeping only known fields (React Flow extras are dropped). */
export function normalizeGraph(input: unknown): Graph {
  if (!isGraphShape(input)) {
    throw new ApiError(400, ErrorCodes.graphSchemaInvalid, 'body.graph must contain "nodes" and "edges" arrays');
  }
  const nodes: GraphNode[] = (input.nodes as unknown[]).map((raw, index) => {
    const node = (raw ?? {}) as Record<string, unknown>;
    const position = (node.position ?? {}) as Record<string, unknown>;
    return {
      id: String(node.id ?? `node_${index}`),
      type: (node.type ?? 'display') as GraphNode['type'],
      position: {
        x: Number.isFinite(Number(position.x)) ? Number(position.x) : 0,
        y: Number.isFinite(Number(position.y)) ? Number(position.y) : 0,
      },
      data: (node.data as Record<string, unknown>) ?? {},
    };
  });

  const edges: GraphEdge[] = (input.edges as unknown[]).map((raw, index) => {
    const edge = (raw ?? {}) as Record<string, unknown>;
    return {
      id: String(edge.id ?? `edge_${index}`),
      source: String(edge.source ?? ''),
      target: String(edge.target ?? ''),
      sourceHandle: String(edge.sourceHandle ?? ''),
      targetHandle: String(edge.targetHandle ?? ''),
    };
  });

  return {
    ...(typeof input.id === 'string' ? { id: input.id } : {}),
    ...(typeof input.name === 'string' ? { name: input.name.slice(0, LIMITS.maxFormulaLength) } : {}),
    nodes,
    edges,
  };
}

export function validateGraph(graph: Graph): ValidationReport {
  const issues = validateGraphStructure(graph);
  const topology = analyzeTopology(graph);
  return {
    ok: issues.length === 0,
    issues,
    topology,
    counts: { nodes: graph.nodes.length, edges: graph.edges.length },
  };
}

/** Fast rejection used by POST /api/graph/execute before a simulation request is made. */
export function assertExecutable(graph: Graph): ValidationReport {
  const report = validateGraph(graph);
  if (graph.nodes.length > LIMITS.maxNodes || graph.edges.length > LIMITS.maxEdges) {
    throw new ApiError(413, ErrorCodes.validationFailed, 'graph exceeds the size limits', {
      details: { maxNodes: LIMITS.maxNodes, maxEdges: LIMITS.maxEdges },
    });
  }
  return report;
}
