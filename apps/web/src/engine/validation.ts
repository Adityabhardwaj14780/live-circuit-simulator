/**
 * Client-side graph rules — the same contract the API and the simulation service enforce, applied
 * while the user is drawing so invalid graphs are hard to build in the first place:
 *
 *   * one edge per input handle (fan-in of exactly one)
 *   * unlimited fan-out from one output
 *   * no self-connections, no unknown handles
 *   * no connection that would close a cycle (defense in depth: the backend still detects cycles)
 */
import { checkConnection, isKnownHandle, type Graph } from '@lng/graph-contract';
import type { Edge, Node } from '@xyflow/react';

export interface ConnectionAttempt {
  source?: string | null;
  target?: string | null;
  sourceHandle?: string | null;
  targetHandle?: string | null;
}

export interface ConnectionVerdict {
  ok: boolean;
  code?: string;
  reason?: string;
}

/** True when `from` can already reach `to` by following edges forward. */
export function reaches(edges: Edge[], from: string, to: string): boolean {
  const adjacency = new Map<string, string[]>();
  for (const edge of edges) {
    const list = adjacency.get(edge.source) ?? [];
    list.push(edge.target);
    adjacency.set(edge.source, list);
  }
  const seen = new Set<string>([from]);
  const queue = [from];
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (current === to) return true;
    for (const next of adjacency.get(current) ?? []) {
      if (seen.has(next)) continue;
      seen.add(next);
      queue.push(next);
    }
  }
  return false;
}

export function validateConnection(nodes: Node[], edges: Edge[], attempt: ConnectionAttempt): ConnectionVerdict {
  const { source, target, sourceHandle, targetHandle } = attempt;
  if (!source || !target || !sourceHandle || !targetHandle) {
    return { ok: false, code: 'UNKNOWN_HANDLE', reason: 'incomplete connection' };
  }
  if (source === target) {
    return { ok: false, code: 'SELF_LOOP', reason: 'a node cannot feed itself' };
  }

  const sourceNode = nodes.find((node) => node.id === source);
  const targetNode = nodes.find((node) => node.id === target);
  if (!sourceNode || !targetNode) {
    return { ok: false, code: 'GRAPH_SCHEMA_INVALID', reason: 'unknown node' };
  }

  // reused contract helper: handles must exist in the right direction
  const graph: Graph = { nodes: [], edges: [] };
  const verdict = checkConnection(graph, { source, target, sourceHandle, targetHandle });
  if (!verdict.ok && verdict.code === 'UNKNOWN_HANDLE') {
    return { ok: false, code: 'UNKNOWN_HANDLE', reason: 'this handle does not exist on the node type' };
  }
  if (!isKnownHandle(String(sourceNode.type), 'output', sourceHandle)) {
    return { ok: false, code: 'UNKNOWN_HANDLE', reason: `"${sourceHandle}" is not an output of ${sourceNode.type}` };
  }
  if (!isKnownHandle(String(targetNode.type), 'input', targetHandle)) {
    return { ok: false, code: 'UNKNOWN_HANDLE', reason: `"${targetHandle}" is not an input of ${targetNode.type}` };
  }

  const occupied = edges.find((edge) => edge.target === target && edge.targetHandle === targetHandle);
  if (occupied) {
    return {
      ok: false,
      code: 'DUPLICATE_INPUT_EDGE',
      reason: `input "${targetHandle}" of ${target} is already connected — one edge per input`,
    };
  }

  // adding source -> target must not close a loop
  if (reaches(edges, target, source)) {
    return { ok: false, code: 'CYCLE_DETECTED', reason: 'this connection would create a feedback loop' };
  }

  return { ok: true };
}

/** Edges that violate the rules (used to highlight a graph loaded from storage or pasted in). */
export function findInvalidEdges(nodes: Node[], edges: Edge[]): { edgeId: string; code: string; reason: string }[] {
  const accepted: Edge[] = [];
  const problems: { edgeId: string; code: string; reason: string }[] = [];
  for (const edge of edges) {
    const verdict = validateConnection(nodes, accepted, {
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle ?? null,
      targetHandle: edge.targetHandle ?? null,
    });
    if (verdict.ok) accepted.push(edge);
    else problems.push({ edgeId: edge.id, code: verdict.code ?? 'VALIDATION_FAILED', reason: verdict.reason ?? 'invalid edge' });
  }
  return problems;
}
