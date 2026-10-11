import type { Graph } from '@lng/graph-contract';
import { createApp } from '../src/app';
import { loadConfig } from '../src/config';
import { MemoryGraphRepository, type GraphRepository } from '../src/db/graphRepository';
import { createLogger } from '../src/logger';
import type { SimClient } from '../src/modules/simClient';
import type { SimulateResponse } from '../src/modules/simContracts';

export const TEST_ENV = {
  NODE_ENV: 'test',
  LOG_LEVEL: 'error',
  API_PORT: '0',
  MONGO_URL: 'mongodb://127.0.0.1:27017',
  MONGO_DB_NAME: 'live_node_graph_test',
  SIMULATION_SERVICE_URL: 'http://simulation.invalid:8000',
} as NodeJS.ProcessEnv;

export const OK_SIMULATION: SimulateResponse = {
  revision: 1,
  status: 'ok',
  results: [
    {
      nodeId: 'n1',
      status: 'ok',
      value: 5,
      errorCode: null,
      message: null,
      source: 'simulation',
      net: 'n1',
      issues: [],
      warnings: [],
      inputStates: {},
    },
  ],
  warnings: [],
  netlist: '* live-node-graph-engine\nV1 n1 0 DC 5\n.op\n.end\n',
  netlistTruncated: false,
  ngspiceVersion: '** ngspice-44.2 : Circuit level simulation program',
  timings: { validationMs: 1, netlistMs: 1, ngspiceMs: 5, parseMs: 1, totalMs: 8 },
  topology: { order: ['n1'], cyclic: [], blocked: [] },
};

export function buildTestApp(options: { repository?: GraphRepository; sim?: Partial<SimClient> } = {}) {
  const config = loadConfig(TEST_ENV);
  const log = createLogger('error');
  const repository = options.repository ?? new MemoryGraphRepository();

  const sim: SimClient = {
    health: async () => ({
      status: 'ok',
      service: 'simulation',
      ngspice: { available: true, version: '** ngspice-44.2 : Circuit level simulation program', binary: 'ngspice', error: null },
      config: {},
      uptimeSeconds: 1,
    }),
    simulate: async () => OK_SIMULATION,
    ...options.sim,
  };

  return { app: createApp({ config, log, repository, sim }), repository, config };
}

export const SIMPLE_GRAPH: Graph = {
  id: 'default',
  name: 'test graph',
  nodes: [
    { id: 'n1', type: 'number', position: { x: 0, y: 0 }, data: { value: 5 } },
    { id: 'd1', type: 'display', position: { x: 200, y: 0 }, data: {} },
  ],
  edges: [{ id: 'e1', source: 'n1', sourceHandle: 'out', target: 'd1', targetHandle: 'in' }],
};

export const CYCLE_GRAPH: Graph = {
  name: 'cycle',
  nodes: [
    { id: 'a1', type: 'add', position: { x: 0, y: 0 }, data: {} },
    { id: 'm1', type: 'multiply', position: { x: 200, y: 0 }, data: {} },
  ],
  edges: [
    { id: 'e1', source: 'a1', sourceHandle: 'out', target: 'm1', targetHandle: 'a' },
    { id: 'e2', source: 'm1', sourceHandle: 'out', target: 'a1', targetHandle: 'a' },
    { id: 'e3', source: 'a1', sourceHandle: 'out', target: 'm1', targetHandle: 'b' },
    { id: 'e4', source: 'm1', sourceHandle: 'out', target: 'a1', targetHandle: 'b' },
  ],
};
