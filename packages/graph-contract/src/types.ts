/** Pure type definitions (no runtime deps) so the runtime modules can import them without cycles. */

export type NodeType = 'number' | 'add' | 'multiply' | 'divide' | 'formula' | 'display';
export type HandleDirection = 'input' | 'output';
export type ErrorCode = string;

export interface GraphNode {
  id: string;
  type: NodeType;
  position: { x: number; y: number };
  data: Record<string, unknown>;
  [extra: string]: unknown;
}

export interface GraphEdge {
  id: string;
  source: string;
  sourceHandle: string;
  target: string;
  targetHandle: string;
  [extra: string]: unknown;
}

export interface Graph {
  id?: string;
  name?: string;
  revision?: number;
  updatedAt?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface Issue {
  code: ErrorCode;
  message: string;
  severity: 'error' | 'warning';
  nodeIds: string[];
  edgeIds: string[];
}

export interface DataFieldSpec {
  kind: 'number' | 'formula';
  required: boolean;
  default: unknown;
  min?: number;
  max?: number;
  maxLength?: number;
}

export interface NodeSpec {
  label: string;
  inputs: string[];
  outputs: string[];
  data: Record<string, DataFieldSpec>;
  notes?: string;
}

export interface Limits {
  maxNodes: number;
  maxEdges: number;
  maxFormulaLength: number;
  maxFormulaDepth: number;
  maxFormulaTokens: number;
}

export interface Topology {
  /** deterministic evaluation order (Kahn, ties broken by node id) */
  order: string[];
  /** nodes that participate in a cycle */
  cyclic: string[];
  /** nodes downstream of a cycle (never evaluated) */
  blocked: string[];
}

export type NodeStatus = 'ok' | 'error' | 'blocked' | 'pending';
