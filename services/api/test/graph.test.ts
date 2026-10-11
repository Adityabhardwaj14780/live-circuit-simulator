import request from 'supertest';
import { describe, expect, it } from 'vitest';
import { CYCLE_GRAPH, SIMPLE_GRAPH, buildTestApp } from './helpers';

describe('graph persistence', () => {
  it('returns an empty graph with revision 0 before anything is saved', async () => {
    const { app } = buildTestApp();
    const response = await request(app).get('/api/graph');
    expect(response.status).toBe(200);
    expect(response.body.revision).toBe(0);
    expect(response.body.graph.nodes).toEqual([]);
  });

  it('saves nodes, positions, data and edges and bumps the revision monotonically', async () => {
    const { app } = buildTestApp();

    const first = await request(app).put('/api/graph').send({ graph: SIMPLE_GRAPH });
    expect(first.status).toBe(200);
    expect(first.body.revision).toBe(1);
    expect(first.body.issues).toEqual([]);

    const second = await request(app).put('/api/graph').send({ graph: SIMPLE_GRAPH });
    expect(second.body.revision).toBe(2);

    const read = await request(app).get('/api/graph');
    expect(read.body.revision).toBe(2);
    expect(read.body.graph.nodes).toHaveLength(2);
    expect(read.body.graph.nodes[0].data.value).toBe(5);
    expect(read.body.graph.nodes[0].position).toEqual({ x: 0, y: 0 });
    expect(read.body.graph.edges[0]).toMatchObject({ source: 'n1', target: 'd1', targetHandle: 'in' });
  });

  it('keeps separate documents per graph id', async () => {
    const { app } = buildTestApp();
    await request(app).put('/api/graph?graphId=alpha').send({ graph: SIMPLE_GRAPH });
    await request(app).put('/api/graph?graphId=beta').send({ graph: { nodes: [], edges: [] } });

    expect((await request(app).get('/api/graph?graphId=alpha')).body.graph.nodes).toHaveLength(2);
    expect((await request(app).get('/api/graph?graphId=beta')).body.graph.nodes).toHaveLength(0);
  });

  it('reports structural issues without losing data', async () => {
    const { app } = buildTestApp();
    const duplicateInput = {
      name: 'duplicate input',
      nodes: [
        { id: 'a', type: 'number', position: { x: 0, y: 0 }, data: { value: 1 } },
        { id: 'b', type: 'number', position: { x: 0, y: 40 }, data: { value: 2 } },
        { id: 'sum', type: 'add', position: { x: 100, y: 0 }, data: {} },
      ],
      edges: [
        { id: 'e1', source: 'a', sourceHandle: 'out', target: 'sum', targetHandle: 'a' },
        { id: 'e2', source: 'b', sourceHandle: 'out', target: 'sum', targetHandle: 'a' },
      ],
    };
    const response = await request(app).put('/api/graph').send({ graph: duplicateInput });
    expect(response.status).toBe(200);
    const codes = response.body.issues.map((issue: { code: string }) => issue.code);
    expect(codes).toContain('DUPLICATE_INPUT_EDGE');
    expect(codes).toContain('MISSING_INPUT');
    expect(response.body.graph.edges).toHaveLength(2); // nothing is silently dropped
  });

  it('rejects bodies that are not graphs', async () => {
    const { app } = buildTestApp();
    const response = await request(app).put('/api/graph').send({ graph: { nodes: 'nope' } });
    expect(response.status).toBe(400);
    expect(response.body.error.code).toBe('GRAPH_SCHEMA_INVALID');
  });

  it('validates graph ids', async () => {
    const { app } = buildTestApp();
    const response = await request(app).get('/api/graph?graphId=../etc/passwd');
    expect(response.status).toBe(400);
    expect(response.body.error.code).toBe('BAD_REQUEST');
  });
});

describe('POST /api/graph/validate', () => {
  it('accepts a valid graph', async () => {
    const { app } = buildTestApp();
    const response = await request(app).post('/api/graph/validate').send({ graph: SIMPLE_GRAPH });
    expect(response.status).toBe(200);
    expect(response.body.ok).toBe(true);
    expect(response.body.counts).toEqual({ nodes: 2, edges: 1 });
  });

  it('detects cycles and reports the cycle members separately from blocked nodes', async () => {
    const { app } = buildTestApp();
    const response = await request(app).post('/api/graph/validate').send({ graph: CYCLE_GRAPH });
    expect(response.body.ok).toBe(false);
    expect(response.body.topology.cyclic.sort()).toEqual(['a1', 'm1']);
    expect(response.body.topology.blocked).toEqual([]);
    expect(response.body.issues.map((issue: { code: string }) => issue.code)).toContain('CYCLE_DETECTED');
  });

  it('rejects unknown handles and self loops', async () => {
    const { app } = buildTestApp();
    const response = await request(app)
      .post('/api/graph/validate')
      .send({
        graph: {
          nodes: [
            { id: 'x', type: 'add', position: { x: 0, y: 0 }, data: {} },
            { id: 'y', type: 'add', position: { x: 200, y: 0 }, data: {} },
          ],
          edges: [
            { id: 'self', source: 'x', sourceHandle: 'out', target: 'x', targetHandle: 'a' },
            { id: 'bad-source', source: 'x', sourceHandle: 'nope', target: 'y', targetHandle: 'a' },
            { id: 'bad-target', source: 'x', sourceHandle: 'out', target: 'y', targetHandle: 'out' },
          ],
        },
      });
    const codes = response.body.issues.map((issue: { code: string }) => issue.code);
    expect(codes).toContain('SELF_LOOP');
    expect(codes.filter((code: string) => code === 'UNKNOWN_HANDLE')).toHaveLength(2);
  });
});
