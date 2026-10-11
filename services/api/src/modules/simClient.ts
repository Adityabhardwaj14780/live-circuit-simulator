/**
 * HTTP client for the simulation service.
 *
 * The browser never talks to Python: every simulation request goes through here. Failures are
 * translated into structured ApiErrors so the frontend can clear stale values and recover:
 *   * connection refused / DNS / abort   -> SIMULATION_SERVICE_UNAVAILABLE (503)
 *   * request timeout                    -> NGSPICE_TIMEOUT (504)
 *   * non-2xx with an error body         -> that code, passed through (502)
 */
import type { Graph } from '@lng/graph-contract';
import type { ApiConfig } from '../config.js';
import type { Logger } from '../logger.js';
import { ApiError, ErrorCodes } from './errors.js';
import { isSimulateResponse, type SimHealth, type SimulateResponse } from './simContracts.js';

export interface SimClient {
  health(): Promise<SimHealth | { status: 'unreachable'; error: string }>;
  simulate(graph: Graph, revision: number, options?: { signal?: AbortSignal }): Promise<SimulateResponse>;
}

export function createSimClient(config: ApiConfig, log: Logger): SimClient {
  const base = config.simulationServiceUrl;

  async function request(path: string, init: RequestInit, timeoutMs: number): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new Error('simulation request timeout')), timeoutMs);
    const externalAbort = init.signal;
    if (externalAbort) {
      externalAbort.addEventListener('abort', () => controller.abort(externalAbort.reason), { once: true });
    }
    try {
      return await fetch(`${base}${path}`, { ...init, signal: controller.signal, headers: { 'content-type': 'application/json', ...(init.headers ?? {}) } });
    } finally {
      clearTimeout(timer);
    }
  }

  return {
    async health() {
      try {
        const response = await request('/health', { method: 'GET' }, Math.min(config.simulationTimeoutMs, 3000));
        if (response.status !== 200 && response.status !== 503) {
          return { status: 'unreachable', error: `unexpected status ${response.status}` } as const;
        }
        return (await response.json()) as SimHealth;
      } catch (error) {
        return { status: 'unreachable', error: error instanceof Error ? error.message : String(error) } as const;
      }
    },

    async simulate(graph, revision, options = {}) {
      let response: Response;
      try {
        response = await request(
          '/simulate',
          { method: 'POST', body: JSON.stringify({ graph, revision }), signal: options.signal },
          config.simulationTimeoutMs,
        );
      } catch (error) {
        const aborted = error instanceof Error && (error.name === 'AbortError' || error.message.includes('timeout'));
        const message = error instanceof Error ? error.message : String(error);
        log.warn('simulation request failed', { code: aborted ? ErrorCodes.simulationTimeout : ErrorCodes.simulationUnavailable, message });
        throw new ApiError(
          aborted ? 504 : 503,
          aborted ? ErrorCodes.simulationTimeout : ErrorCodes.simulationUnavailable,
          aborted
            ? `simulation request exceeded ${config.simulationTimeoutMs} ms`
            : `simulation service is unavailable: ${message}`,
          { details: { simulationServiceUrl: base } },
        );
      }

      const payload = (await response.json().catch(() => null)) as unknown;
      if (!response.ok) {
        const body = payload as { error?: { code?: string; message?: string; nodeIds?: string[]; edgeIds?: string[]; details?: Record<string, unknown> } } | null;
        const code = body?.error?.code ?? ErrorCodes.simulationError;
        log.warn('simulation returned an error', { status: response.status, code });
        throw new ApiError(502, code, body?.error?.message ?? `simulation failed with status ${response.status}`, {
          nodeIds: body?.error?.nodeIds ?? [],
          edgeIds: body?.error?.edgeIds ?? [],
          details: body?.error?.details,
        });
      }

      if (!isSimulateResponse(payload)) {
        throw new ApiError(502, ErrorCodes.simulationError, 'simulation service returned an unexpected payload');
      }
      return payload;
    },
  };
}
