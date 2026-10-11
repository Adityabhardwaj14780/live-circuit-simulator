import request from 'supertest';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../src/modules/errors';
import { OK_SIMULATION, SIMPLE_GRAPH, buildTestApp } from './helpers';

describe('POST /api/graph/execute', () => {
  it('proxies the graph and revision and returns per-node results', async () => {
    const simulate = vi.fn(async () => ({
      revision: 7,
      status: 'ok' as const,
      results: [
        {
          nodeId: 'n1',
          status: 'ok' as const,
          value: 5,
          errorCode: null,
          message: null,
          source: 'simulation' as const,
          net: 'n1',
          issues: [],
          warnings: [],
          inputStates: {},
        },
      ],
      warnings: [],
      netlist: 'V1 n1 0 DC 5\n.op\n.end\n',
      netlistTruncated: false,
      ngspiceVersion: '** ngspice-44.2 : Circuit level simulation program',
      timings: { validationMs: 0, netlistMs: 0, ngspiceMs: 1, parseMs: 0, totalMs: 1 },
      topology: {},
    }));

    const { app } = buildTestApp({ sim: { simulate } });
    const response = await request(app).post('/api/graph/execute').send({ graph: SIMPLE_GRAPH, revision: 7 });

    expect(response.status).toBe(200);
    expect(response.body.results[0].nodeId).toBe('n1');
    expect(response.body.revision).toBe(7);
    expect(simulate).toHaveBeenCalledOnce();
    const [forwardedGraph, forwardedRevision] = simulate.mock.calls[0] as unknown as [typeof SIMPLE_GRAPH, number];
    expect(forwardedRevision).toBe(7);
    expect(forwardedGraph.nodes).toHaveLength(2);
  });

  it('echoes the revision of each concurrent request independently', async () => {
    // Two in-flight runs must not share revision state: the client uses the echoed revision to
    // decide whether a response is still current (stale-response prevention at the transport level).
    const delays: Record<number, number> = { 1: 60, 2: 5 };
    const seen: number[] = [];
    const simulate = vi.fn(async (_graph: unknown, revision: number) => {
      seen.push(revision);
      await new Promise((resolve) => setTimeout(resolve, delays[revision] ?? 0));
      return { ...OK_SIMULATION, revision };
    });

    const { app } = buildTestApp({ sim: { simulate } });
    const [first, second] = await Promise.all([
      request(app).post('/api/graph/execute').send({ graph: SIMPLE_GRAPH, revision: 1 }),
      request(app).post('/api/graph/execute').send({ graph: SIMPLE_GRAPH, revision: 2 }),
    ]);

    expect(seen.sort()).toEqual([1, 2]);
    expect(first.body.revision).toBe(1);
    expect(second.body.revision).toBe(2);
    // the slower (older) request answering last must still carry its own revision
    expect(first.body.results).toHaveLength(1);
    expect(second.body.results).toHaveLength(1);
  });

  it('returns 503 with a structured error when the simulation service is unavailable', async () => {
    const { app } = buildTestApp({
      sim: {
        simulate: async () => {
          throw new ApiError(503, 'SIMULATION_SERVICE_UNAVAILABLE', 'simulation service is unavailable: ECONNREFUSED');
        },
      },
    });
    const response = await request(app).post('/api/graph/execute').send({ graph: SIMPLE_GRAPH, revision: 1 });
    expect(response.status).toBe(503);
    expect(response.body.error.code).toBe('SIMULATION_SERVICE_UNAVAILABLE');
    expect(response.body.error.nodeIds).toEqual([]);
  });

  it('returns 504 when the simulation request times out', async () => {
    const { app } = buildTestApp({
      sim: {
        simulate: async () => {
          throw new ApiError(504, 'NGSPICE_TIMEOUT', 'simulation request exceeded 15000 ms');
        },
      },
    });
    const response = await request(app).post('/api/graph/execute').send({ graph: SIMPLE_GRAPH, revision: 2 });
    expect(response.status).toBe(504);
    expect(response.body.error.code).toBe('NGSPICE_TIMEOUT');
  });

  it('passes simulation error codes through with node ids', async () => {
    const { app } = buildTestApp({
      sim: {
        simulate: async () => {
          throw new ApiError(502, 'NGSPICE_ERROR', 'ngspice exited with code 1', { nodeIds: ['add1'] });
        },
      },
    });
    const response = await request(app).post('/api/graph/execute').send({ graph: SIMPLE_GRAPH, revision: 3 });
    expect(response.status).toBe(502);
    expect(response.body.error.code).toBe('NGSPICE_ERROR');
    expect(response.body.error.nodeIds).toEqual(['add1']);
  });

  it('rejects oversized graphs before calling the simulation service', async () => {
    const simulate = vi.fn();
    const { app } = buildTestApp({ sim: { simulate } });
    const nodes = Array.from({ length: 250 }, (_, index) => ({
      id: `n${index}`,
      type: 'number',
      position: { x: 0, y: 0 },
      data: { value: index },
    }));
    const response = await request(app).post('/api/graph/execute').send({ graph: { nodes, edges: [] }, revision: 1 });
    expect(response.status).toBe(413);
    expect(response.body.error.code).toBe('VALIDATION_FAILED');
    expect(simulate).not.toHaveBeenCalled();
  });

  it('rejects a missing graph body with 400', async () => {
    const { app } = buildTestApp();
    const response = await request(app).post('/api/graph/execute').send({ revision: 1 });
    expect(response.status).toBe(400);
    expect(response.body.error.code).toBe('GRAPH_SCHEMA_INVALID');
  });
});
