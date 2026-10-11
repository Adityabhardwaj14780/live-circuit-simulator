/**
 * TypeScript mirror of the FastAPI response schema (services/sim/app/schemas.py).
 * Kept explicit so the API does not pass through untyped JSON.
 */

export type NodeStatus = 'ok' | 'error' | 'blocked';
export type RunStatus = 'ok' | 'partial' | 'error';

export interface NodeIssue {
  code: string;
  message: string;
  severity: 'error' | 'warning';
  handle?: string | null;
}

export interface SimNodeResult {
  nodeId: string;
  status: NodeStatus;
  value: number | null;
  errorCode: string | null;
  message: string | null;
  source: 'literal' | 'simulation' | 'none' | null;
  net: string | null;
  issues: NodeIssue[];
  warnings: string[];
  inputStates: Record<string, string>;
}

export interface SimWarning {
  code: string;
  message: string;
  nodeIds: string[];
  edgeIds: string[];
}

export interface SimTimings {
  validationMs: number;
  netlistMs: number;
  ngspiceMs: number;
  parseMs: number;
  totalMs: number;
}

export interface SimulateResponse {
  revision: number;
  status: RunStatus;
  results: SimNodeResult[];
  warnings: SimWarning[];
  netlist: string | null;
  netlistTruncated: boolean;
  ngspiceVersion: string | null;
  timings: SimTimings;
  topology: { order?: string[]; cyclic?: string[]; blocked?: string[] };
  message?: string | null;
}

export interface SimHealth {
  status: 'ok' | 'degraded';
  service: string;
  ngspice: { available: boolean; version: string | null; binary: string; error: string | null };
  config: Record<string, unknown>;
  uptimeSeconds: number;
}

export function isSimulateResponse(value: unknown): value is SimulateResponse {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<SimulateResponse>;
  return typeof candidate.revision === 'number' && Array.isArray(candidate.results) && typeof candidate.status === 'string';
}
