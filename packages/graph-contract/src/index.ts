/**
 * Graph contract — single source of truth (mirrored by services/sim/app/schemas.py).
 *
 * graph.schema.json is canonical; the registries and types below are derived from it so the web app,
 * the Express API and the Python simulation service cannot drift apart silently.
 *
 * SECURITY NOTE: this module (and every other TS module in the repo) performs *structural* validation
 * only. Formula expressions are parsed and evaluated exclusively in Python (services/sim/app/formula.py)
 * with a hand-written tokenizer/parser — `eval`/`exec` are never used anywhere in this project.
 */
import rawSchema from '../graph.schema.json';
import type { ErrorCode, Graph, GraphNode, HandleDirection, Issue, Limits, NodeSpec, NodeType, Topology } from './types';

export type {
  DataFieldSpec,
  ErrorCode,
  Graph,
  GraphEdge,
  GraphNode,
  HandleDirection,
  Issue,
  Limits,
  NodeSpec,
  NodeStatus,
  NodeType,
  Topology,
} from './types';

interface RawSchema {
  'x-node-handles': Record<NodeType, NodeSpec>;
  'x-error-codes': string[];
  'x-limits': Limits;
}

const schema = rawSchema as unknown as RawSchema;

export const NODE_SPECS: Record<NodeType, NodeSpec> = schema['x-node-handles'];
export const NODE_TYPES = Object.keys(NODE_SPECS) as NodeType[];
export const ERROR_CODES: ErrorCode[] = schema['x-error-codes'];
export const LIMITS: Limits = schema['x-limits'];
export const GRAPH_SCHEMA = rawSchema;

export function getNodeSpec(type: string): NodeSpec | undefined {
  return (NODE_SPECS as Record<string, NodeSpec>)[type];
}

export function isNodeType(value: unknown): value is NodeType {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(NODE_SPECS, value);
}

/** True when `handle` exists on `type` in the given direction. */
export function isKnownHandle(type: string, direction: HandleDirection, handle: string): boolean {
  const spec = getNodeSpec(type);
  if (!spec) return false;
  const list = direction === 'input' ? spec.inputs : spec.outputs;
  return list.includes(handle);
}

export function defaultDataForType(type: NodeType): Record<string, unknown> {
  const spec = getNodeSpec(type);
  const data: Record<string, unknown> = {};
  if (!spec) return data;
  for (const [key, field] of Object.entries(spec.data)) data[key] = field.default;
  return data;
}

/** Structural + connectivity validation (authoritative check: services/sim/app/graph.py). */
export function validateGraphStructure(graph: Graph): Issue[] {
  const issues: Issue[] = [];
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph?.edges) ? graph.edges : [];

  if (nodes.length > LIMITS.maxNodes) {
    issues.push(issue('GRAPH_SCHEMA_INVALID', `graph has ${nodes.length} nodes, limit is ${LIMITS.maxNodes}`));
  }
  if (edges.length > LIMITS.maxEdges) {
    issues.push(issue('GRAPH_SCHEMA_INVALID', `graph has ${edges.length} edges, limit is ${LIMITS.maxEdges}`));
  }

  const byId = new Map<string, GraphNode>();
  for (const node of nodes) {
    if (!node || typeof node.id !== 'string' || node.id.length === 0) {
      issues.push(issue('GRAPH_SCHEMA_INVALID', 'node is missing a string id'));
      continue;
    }
    if (byId.has(node.id)) {
      issues.push(issue('GRAPH_SCHEMA_INVALID', `duplicate node id "${node.id}"`, [node.id]));
      continue;
    }
    byId.set(node.id, node);

    if (!isNodeType(node.type)) {
      issues.push(issue('UNKNOWN_NODE_TYPE', `unknown node type "${String(node.type)}"`, [node.id]));
      continue;
    }
    if (!node.position || typeof node.position.x !== 'number' || typeof node.position.y !== 'number') {
      issues.push(issue('GRAPH_SCHEMA_INVALID', 'node position must be {x: number, y: number}', [node.id]));
    }
    issues.push(...validateNodeData(node));
  }

  const connectedInputs = new Map<string, string>(); // "nodeId:handle" -> edgeId
  const seenEdgeIds = new Set<string>();
  for (const edge of edges) {
    if (!edge || typeof edge.id !== 'string' || edge.id.length === 0) {
      issues.push(issue('GRAPH_SCHEMA_INVALID', 'edge is missing a string id'));
      continue;
    }
    if (seenEdgeIds.has(edge.id)) {
      issues.push(issue('GRAPH_SCHEMA_INVALID', `duplicate edge id "${edge.id}"`, [], [edge.id]));
      continue;
    }
    seenEdgeIds.add(edge.id);

    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target) {
      issues.push(
        issue('GRAPH_SCHEMA_INVALID', `edge "${edge.id}" references a missing node`, [edge.source, edge.target], [edge.id]),
      );
      continue;
    }
    if (edge.source === edge.target) {
      issues.push(issue('SELF_LOOP', 'self-connections are not allowed', [edge.source], [edge.id]));
      continue;
    }
    if (!isKnownHandle(source.type, 'output', edge.sourceHandle)) {
      issues.push(
        issue(
          'UNKNOWN_HANDLE',
          `node "${source.id}" (${source.type}) has no output handle "${String(edge.sourceHandle)}"`,
          [source.id],
          [edge.id],
        ),
      );
      continue;
    }
    if (!isKnownHandle(target.type, 'input', edge.targetHandle)) {
      issues.push(
        issue(
          'UNKNOWN_HANDLE',
          `node "${target.id}" (${target.type}) has no input handle "${String(edge.targetHandle)}"`,
          [target.id],
          [edge.id],
        ),
      );
      continue;
    }
    const key = `${edge.target}:${edge.targetHandle}`;
    if (connectedInputs.has(key)) {
      issues.push(
        issue(
          'DUPLICATE_INPUT_EDGE',
          `input "${edge.targetHandle}" of node "${target.id}" already has an edge`,
          [target.id],
          [edge.id],
        ),
      );
      continue;
    }
    connectedInputs.set(key, edge.id);
  }

  for (const node of nodes) {
    if (!isNodeType(node.type)) continue;
    const spec = NODE_SPECS[node.type];
    for (const handle of spec.inputs) {
      // formula inputs are validated by the expression parser: only referenced identifiers must be wired
      if (node.type === 'formula') continue;
      if (!connectedInputs.has(`${node.id}:${handle}`)) {
        issues.push(issue('MISSING_INPUT', `node "${node.id}" (${node.type}) is missing input "${handle}"`, [node.id]));
      }
    }
  }

  // Cycles are reported here too (mirroring services/sim/app/graph.py) so validate-only callers see
  // the same picture the simulation service would refuse to execute.
  const topology = analyzeTopology({ nodes, edges });
  for (const id of topology.cyclic) {
    issues.push(issue('CYCLE_DETECTED', `node "${id}" is part of a feedback cycle and cannot be simulated`, [id]));
  }

  return issues;
}

function validateNodeData(node: GraphNode): Issue[] {
  const spec = getNodeSpec(node.type);
  if (!spec) return [];
  const issues: Issue[] = [];
  const data = (node.data ?? {}) as Record<string, unknown>;
  for (const [key, field] of Object.entries(spec.data)) {
    const value = data[key];
    if (value === undefined || value === null || value === '') {
      if (field.required) {
        issues.push(issue('GRAPH_SCHEMA_INVALID', `node "${node.id}" is missing required data.${key}`, [node.id]));
      }
      continue;
    }
    if (field.kind === 'number') {
      const numeric = typeof value === 'number' ? value : Number(value);
      if (typeof value === 'boolean' || !Number.isFinite(numeric)) {
        issues.push(issue('NON_NUMERIC_VALUE', `node "${node.id}" data.${key} must be a finite number`, [node.id]));
      } else if (field.min !== undefined && numeric < field.min) {
        issues.push(issue('NON_NUMERIC_VALUE', `node "${node.id}" data.${key} is below the minimum ${field.min}`, [node.id]));
      } else if (field.max !== undefined && numeric > field.max) {
        issues.push(issue('NON_NUMERIC_VALUE', `node "${node.id}" data.${key} is above the maximum ${field.max}`, [node.id]));
      }
    }
    if (field.kind === 'formula') {
      if (typeof value !== 'string') {
        issues.push(issue('FORMULA_SYNTAX_ERROR', `node "${node.id}" data.${key} must be a string`, [node.id]));
      } else if (value.length > LIMITS.maxFormulaLength) {
        issues.push(
          issue('FORMULA_SYNTAX_ERROR', `node "${node.id}" expression exceeds ${LIMITS.maxFormulaLength} characters`, [node.id]),
        );
      }
    }
  }
  return issues;
}

/**
 * Cycle + ordering analysis.
 *
 * A plain Kahn peel is not enough: every node downstream of a cycle also keeps a non-zero in-degree,
 * so it would be misreported as a cycle member. We therefore classify with strongly connected
 * components (Tarjan, iterative) and only then treat the descendants of cycle members as blocked.
 * The evaluation order is a Kahn sort restricted to the healthy subgraph, ties broken by node id.
 */
export function analyzeTopology(graph: Graph): Topology {
  const ids = graph.nodes.map((n) => n.id).filter((id) => typeof id === 'string');
  const idSet = new Set(ids);
  const outAdj = new Map<string, string[]>(ids.map((id) => [id, []]));
  const selfLooped = new Set<string>();

  for (const edge of graph.edges) {
    if (!idSet.has(edge.source) || !idSet.has(edge.target)) continue;
    if (edge.source === edge.target) {
      selfLooped.add(edge.source); // defensive: edges are rejected earlier, never executed
      continue;
    }
    outAdj.get(edge.source)!.push(edge.target);
  }
  for (const list of outAdj.values()) list.sort();

  const componentOf = tarjanScc(ids, outAdj);
  const cyclic = new Set<string>();
  const componentSize = new Map<number, number>();
  for (const id of ids) {
    const c = componentOf.get(id)!;
    componentSize.set(c, (componentSize.get(c) ?? 0) + 1);
  }
  for (const id of ids) {
    const c = componentOf.get(id)!;
    if ((componentSize.get(c) ?? 0) > 1 || selfLooped.has(id)) cyclic.add(id);
  }

  const blocked = new Set<string>();
  const queue = [...cyclic];
  while (queue.length > 0) {
    const id = queue.shift()!;
    for (const next of outAdj.get(id) ?? []) {
      if (cyclic.has(next) || blocked.has(next)) continue;
      blocked.add(next);
      queue.push(next);
    }
  }

  // Kahn ordering on the healthy induced subgraph (deterministic, ties broken by id)
  const healthy = ids.filter((id) => !cyclic.has(id) && !blocked.has(id)).sort();
  const healthySet = new Set(healthy);
  const indegree = new Map<string, number>(healthy.map((id) => [id, 0]));
  for (const id of healthy) {
    for (const next of outAdj.get(id) ?? []) {
      if (healthySet.has(next)) indegree.set(next, (indegree.get(next) ?? 0) + 1);
    }
  }
  const ready = healthy.filter((id) => (indegree.get(id) ?? 0) === 0);
  const order: string[] = [];
  while (ready.length > 0) {
    const id = ready.shift()!;
    order.push(id);
    for (const next of outAdj.get(id) ?? []) {
      if (!healthySet.has(next)) continue;
      const remaining = (indegree.get(next) ?? 0) - 1;
      indegree.set(next, remaining);
      if (remaining === 0) {
        ready.push(next);
        ready.sort();
      }
    }
  }

  return { order, cyclic: [...cyclic].sort(), blocked: [...blocked].sort() };
}

/** Iterative Tarjan SCC (no recursion depth risk on large graphs). */
function tarjanScc(ids: string[], outAdj: Map<string, string[]>): Map<string, number> {
  const index = new Map<string, number>();
  const low = new Map<string, number>();
  const onStack = new Set<string>();
  const stack: string[] = [];
  const componentOf = new Map<string, number>();
  let counter = 0;
  let componentCount = 0;

  for (const root of ids) {
    if (index.has(root)) continue;
    const work: Array<{ node: string; cursor: number }> = [{ node: root, cursor: 0 }];
    index.set(root, counter);
    low.set(root, counter);
    counter += 1;
    stack.push(root);
    onStack.add(root);

    while (work.length > 0) {
      const frame = work[work.length - 1]!;
      const neighbours = outAdj.get(frame.node) ?? [];
      if (frame.cursor < neighbours.length) {
        const next = neighbours[frame.cursor]!;
        frame.cursor += 1;
        if (!index.has(next)) {
          index.set(next, counter);
          low.set(next, counter);
          counter += 1;
          stack.push(next);
          onStack.add(next);
          work.push({ node: next, cursor: 0 });
        } else if (onStack.has(next)) {
          low.set(frame.node, Math.min(low.get(frame.node)!, index.get(next)!));
        }
        continue;
      }

      work.pop();
      if (work.length > 0) {
        const parentFrame = work[work.length - 1]!;
        low.set(parentFrame.node, Math.min(low.get(parentFrame.node)!, low.get(frame.node)!));
      }
      if (low.get(frame.node) === index.get(frame.node)) {
        for (;;) {
          const member = stack.pop()!;
          onStack.delete(member);
          componentOf.set(member, componentCount);
          if (member === frame.node) break;
        }
        componentCount += 1;
      }
    }
  }
  return componentOf;
}

/** Connection rule used by the canvas (`isValidConnection`) and mirrored in the API. */
export function checkConnection(
  graph: Graph,
  connection: { source?: string | null; target?: string | null; sourceHandle?: string | null; targetHandle?: string | null },
): { ok: boolean; code?: ErrorCode; reason?: string } {
  const { source, target, sourceHandle, targetHandle } = connection;
  if (!source || !target || !sourceHandle || !targetHandle) {
    return { ok: false, code: 'UNKNOWN_HANDLE', reason: 'incomplete connection' };
  }
  if (source === target) return { ok: false, code: 'SELF_LOOP', reason: 'a node cannot feed itself' };
  const sourceNode = graph.nodes.find((n) => n.id === source);
  const targetNode = graph.nodes.find((n) => n.id === target);
  if (!sourceNode || !targetNode) return { ok: false, code: 'GRAPH_SCHEMA_INVALID', reason: 'unknown node' };
  if (!isKnownHandle(sourceNode.type, 'output', sourceHandle)) {
    return { ok: false, code: 'UNKNOWN_HANDLE', reason: `"${sourceHandle}" is not an output of ${sourceNode.type}` };
  }
  if (!isKnownHandle(targetNode.type, 'input', targetHandle)) {
    return { ok: false, code: 'UNKNOWN_HANDLE', reason: `"${targetHandle}" is not an input of ${targetNode.type}` };
  }
  const occupied = graph.edges.find((e) => e.target === target && e.targetHandle === targetHandle);
  if (occupied) {
    return { ok: false, code: 'DUPLICATE_INPUT_EDGE', reason: `input "${targetHandle}" is already connected` };
  }
  return { ok: true };
}

/** Sample graph used by tests, the API default document and the UI "load example" action. */
export const EXAMPLE_GRAPH: Graph = {
  name: 'example: (2 + 3) * 4 and a / b + 1',
  nodes: [
    { id: 'n1', type: 'number', position: { x: 0, y: 0 }, data: { value: 2 } },
    { id: 'n2', type: 'number', position: { x: 0, y: 140 }, data: { value: 3 } },
    { id: 'n3', type: 'add', position: { x: 240, y: 60 }, data: {} },
    { id: 'n4', type: 'number', position: { x: 0, y: 280 }, data: { value: 4 } },
    { id: 'n5', type: 'multiply', position: { x: 480, y: 140 }, data: {} },
    { id: 'n6', type: 'formula', position: { x: 480, y: 320 }, data: { expression: 'a / b + 1' } },
    { id: 'n7', type: 'display', position: { x: 760, y: 140 }, data: {} },
  ],
  edges: [
    { id: 'e1', source: 'n1', sourceHandle: 'out', target: 'n3', targetHandle: 'a' },
    { id: 'e2', source: 'n2', sourceHandle: 'out', target: 'n3', targetHandle: 'b' },
    { id: 'e3', source: 'n3', sourceHandle: 'out', target: 'n5', targetHandle: 'a' },
    { id: 'e4', source: 'n4', sourceHandle: 'out', target: 'n5', targetHandle: 'b' },
    { id: 'e5', source: 'n3', sourceHandle: 'out', target: 'n6', targetHandle: 'a' },
    { id: 'e6', source: 'n4', sourceHandle: 'out', target: 'n6', targetHandle: 'b' },
    { id: 'e7', source: 'n5', sourceHandle: 'out', target: 'n7', targetHandle: 'in' },
  ],
};

/** Stable, non-throwing structural check used by the API before it forwards to Python. */
export function isGraphShape(value: unknown): value is Graph {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<Graph>;
  return Array.isArray(candidate.nodes) && Array.isArray(candidate.edges);
}

function issue(code: ErrorCode, message: string, nodeIds: string[] = [], edgeIds: string[] = []): Issue {
  return { code, message, severity: 'error', nodeIds, edgeIds };
}

export * from './netlistHints';
