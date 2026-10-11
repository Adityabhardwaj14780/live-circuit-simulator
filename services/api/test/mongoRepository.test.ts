/**
 * Real-MongoDB tests via mongodb-memory-server (no Docker required here).
 * The binary is downloaded and cached on first run; the socket is bound to 127.0.0.1 only.
 */
import { MongoClient } from 'mongodb';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { MongoGraphRepository } from '../src/db/graphRepository';
import { SIMPLE_GRAPH } from './helpers';

let server: MongoMemoryServer;
let client: MongoClient;
let repository: MongoGraphRepository;

beforeAll(async () => {
  server = await MongoMemoryServer.create({ instance: { ip: '127.0.0.1' } });
  client = new MongoClient(server.getUri());
  await client.connect();
  repository = new MongoGraphRepository(client, client.db('live_node_graph_test'));
});

afterAll(async () => {
  await client.close();
  await server.stop();
});

describe('MongoGraphRepository', () => {
  it('returns an empty graph document for an unknown id', async () => {
    const stored = await repository.read('missing');
    expect(stored.revision).toBe(0);
    expect(stored.graph.nodes).toEqual([]);
  });

  it('persists and restores nodes, data, positions and edges', async () => {
    const saved = await repository.save('default', SIMPLE_GRAPH);
    expect(saved.revision).toBe(1);

    const read = await repository.read('default');
    expect(read.revision).toBe(1);
    expect(read.graph.nodes).toEqual(SIMPLE_GRAPH.nodes);
    expect(read.graph.edges).toEqual(SIMPLE_GRAPH.edges);
    expect(read.updatedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  it('bumps the revision on every save', async () => {
    await repository.save('counter', SIMPLE_GRAPH);
    await repository.save('counter', SIMPLE_GRAPH);
    const third = await repository.save('counter', SPEC({ value: 42 }));
    expect(third.revision).toBe(3);
    const read = await repository.read('counter');
    expect(read.revision).toBe(3);
    expect(read.graph.nodes[0]?.data.value).toBe(42);
  });

  it('reports healthy for ping', async () => {
    await expect(repository.ping()).resolves.toBe(true);
  });
});

function SPEC(overrides: { value: number }) {
  return {
    name: 'updated',
    nodes: [{ id: 'n1', type: 'number' as const, position: { x: 1, y: 2 }, data: { value: overrides.value } }],
    edges: [],
  };
}
