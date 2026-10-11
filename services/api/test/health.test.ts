import request from 'supertest';
import { describe, expect, it } from 'vitest';
import { MemoryGraphRepository } from '../src/db/graphRepository';
import { buildTestApp } from './helpers';

describe('GET /health', () => {
  it('reports ok when mongo and the simulation service are healthy', async () => {
    const { app } = buildTestApp();
    const response = await request(app).get('/health');
    expect(response.status).toBe(200);
    expect(response.body.status).toBe('ok');
    expect(response.body.mongo.ok).toBe(true);
    expect(response.body.simulation.ngspice.available).toBe(true);
    expect(typeof response.body.uptimeSeconds).toBe('number');
  });

  it('reports degraded (still 200) when the simulation service is unreachable', async () => {
    const { app } = buildTestApp({ sim: { health: async () => ({ status: 'unreachable', error: 'ECONNREFUSED' }) } });
    const response = await request(app).get('/health');
    expect(response.status).toBe(200);
    expect(response.body.status).toBe('degraded');
    expect(response.body.simulation.status).toBe('unreachable');
  });

  it('reports error with 503 when mongo is down', async () => {
    class DownRepository extends MemoryGraphRepository {
      override async ping(): Promise<boolean> {
        return false;
      }
    }
    const { app } = buildTestApp({ repository: new DownRepository() });
    const response = await request(app).get('/health');
    expect(response.status).toBe(503);
    expect(response.body.status).toBe('error');
    expect(response.body.mongo.ok).toBe(false);
  });
});

describe('routes', () => {
  it('serves an index and a structured 404', async () => {
    const { app } = buildTestApp();
    expect((await request(app).get('/')).body.service).toBe('api');

    const missing = await request(app).get('/nope');
    expect(missing.status).toBe(404);
    expect(missing.body.error.code).toBe('NOT_FOUND');
    expect(missing.body.error.nodeIds).toEqual([]);
  });

  it('exposes the node contract at /api/meta', async () => {
    const { app } = buildTestApp();
    const response = await request(app).get('/api/meta');
    expect(response.status).toBe(200);
    expect(Object.keys(response.body.nodeSpecs).sort()).toEqual(['add', 'display', 'divide', 'formula', 'multiply', 'number']);
    expect(response.body.limits.maxNodes).toBe(200);
    expect(response.body.netlistHints.divide.template).toContain('V({in_a}) / V({in_b})');
  });
});
