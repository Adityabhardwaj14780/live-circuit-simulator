/**
 * The only network module in the frontend.
 *
 * Every call goes to the Express API under a relative path (`/api/...`), which the dev server and the
 * nginx image proxy to the API container. The browser never talks to FastAPI, MongoDB or ngspice
 * directly, and no service URL is therefore exposed to the client bundle.
 */
import type { Graph } from '@lng/graph-contract';
import type { LiveError, LiveResult } from '../engine/liveSync';

const BASE = ''; // relative: same origin, proxied to the Express API

export interface StoredGraphResponse {
  graphId: string;
  graph: Graph;
  revision: number;
  updatedAt: string;
  issues?: { code: string; message: string; nodeIds: string[]; edgeIds: string[] }[];
}

export interface HealthResponse {
  status: 'ok' | 'degraded' | 'error';
  mongo: { ok: boolean; db: string };
  simulation: { status: string; ngspice?: { available: boolean; version: string | null; error: string | null } };
  uptimeSeconds: number;
}

export class ApiClientError extends Error implements LiveError {
  readonly code: string;
  readonly nodeIds: string[];
  readonly edgeIds: string[];
  readonly status: number;

  constructor(status: number, code: string, message: string, nodeIds: string[] = [], edgeIds: string[] = []) {
    super(message);
    this.name = 'ApiClientError';
    this.status = status;
    this.code = code;
    this.nodeIds = nodeIds;
    this.edgeIds = edgeIds;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { 'content-type': 'application/json', ...(init.headers ?? {}) },
    });
  } catch (error) {
    if ((error as { name?: string })?.name === 'AbortError') throw error;
    throw new ApiClientError(0, 'API_UNREACHABLE', `cannot reach the API: ${(error as Error).message}`);
  }

  const payload = (await response.json().catch(() => null)) as
    | (T & { error?: { code: string; message: string; nodeIds: string[]; edgeIds: string[] } })
    | null;

  if (!response.ok) {
    const body = payload?.error;
    throw new ApiClientError(
      response.status,
      body?.code ?? 'API_ERROR',
      body?.message ?? `request failed with status ${response.status}`,
      body?.nodeIds ?? [],
      body?.edgeIds ?? [],
    );
  }
  return payload as T;
}

export const api = {
  health: () => request<HealthResponse>('/health'),

  loadGraph: (graphId = 'default') => request<StoredGraphResponse>(`/api/graph?graphId=${encodeURIComponent(graphId)}`),

  saveGraph: (graph: Graph, graphId = 'default') =>
    request<StoredGraphResponse>(`/api/graph?graphId=${encodeURIComponent(graphId)}`, {
      method: 'PUT',
      body: JSON.stringify({ graph }),
    }),

  validate: (graph: Graph) =>
    request<{ ok: boolean; issues: unknown[]; topology: { order: string[]; cyclic: string[]; blocked: string[] } }>(
      '/api/graph/validate',
      { method: 'POST', body: JSON.stringify({ graph }) },
    ),

  /** Executes through Express → FastAPI → ngspice. `signal` lets the live-sync engine cancel it. */
  execute: (graph: Graph, revision: number, signal?: AbortSignal) =>
    request<LiveResult>('/api/graph/execute', { method: 'POST', body: JSON.stringify({ graph, revision }), signal }),

  meta: () => request<{ nodeSpecs: Record<string, unknown>; limits: Record<string, number> }>('/api/meta'),
};
