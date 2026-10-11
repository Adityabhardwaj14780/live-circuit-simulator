/**
 * @vitest-environment jsdom
 *
 * Regression test for the hook lifecycle.
 *
 * React StrictMode mounts, unmounts and re-mounts every effect in development. An earlier version
 * created the LiveSyncEngine in a ref initialiser and disposed it in the effect cleanup, so the
 * second mount subscribed to `null` and the whole editor crashed in `npm run dev` (production builds
 * hid it because StrictMode does not double-invoke there). This test renders the hook under
 * StrictMode and asserts it reaches a live result.
 */
import { StrictMode } from 'react';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Edge, Node } from '@xyflow/react';

import { useLiveGraph } from './useLiveGraph';

const execute = vi.fn();
const saveGraph = vi.fn();

vi.mock('../api/client', () => ({
  api: {
    execute: (...args: unknown[]) => execute(...args),
    saveGraph: (...args: unknown[]) => saveGraph(...args),
  },
}));

const nodes: Node[] = [
  { id: 'n1', type: 'number', position: { x: 0, y: 0 }, data: { value: 2 } },
  { id: 'd1', type: 'display', position: { x: 200, y: 0 }, data: {} },
];
const edges: Edge[] = [{ id: 'e1', source: 'n1', target: 'd1', sourceHandle: 'out', targetHandle: 'in' }];

function Probe() {
  const { snapshot } = useLiveGraph({ nodes, edges, graphName: 'hook test' });
  return (
    <div>
      <span data-testid="phase">{snapshot.phase}</span>
      <span data-testid="runs">{snapshot.runs}</span>
      <span data-testid="value">{String(snapshot.result?.results?.[0]?.value ?? '')}</span>
    </div>
  );
}

describe('useLiveGraph under React StrictMode', () => {
  beforeEach(() => {
    execute.mockReset();
    saveGraph.mockReset();
    saveGraph.mockResolvedValue({ revision: 1, updatedAt: new Date().toISOString(), graphId: 'default', graph: { nodes: [], edges: [] } });
    execute.mockImplementation(async (_graph: unknown, revision: number) => ({
      revision,
      status: 'ok' as const,
      results: [
        {
          nodeId: 'n1',
          status: 'ok' as const,
          value: 42,
          errorCode: null,
          message: null,
          net: 'n1',
          issues: [],
          warnings: [],
          inputStates: {},
        },
      ],
      warnings: [],
      netlist: 'V1 n1 0 DC 42\n.op\n.end\n',
      ngspiceVersion: 'ngspice-44.2',
      timings: { validationMs: 0, netlistMs: 0, ngspiceMs: 1, parseMs: 0, totalMs: 1 },
      topology: {},
    }));
  });

  afterEach(() => {
    cleanup();  // vitest runs without globals here, so testing-library cannot auto-clean
    vi.useRealTimers();
  });

  it('survives the mount/unmount/remount cycle and publishes results', async () => {
    render(
      <StrictMode>
        <Probe />
      </StrictMode>,
    );

    await waitFor(() => expect(screen.getByTestId('phase').textContent).toBe('ok'), { timeout: 5000 });
    expect(screen.getByTestId('value').textContent).toBe('42');
    expect(execute).toHaveBeenCalled();
  });

  it('runs exactly one live execution per edit, even with StrictMode re-mounting', async () => {
    render(
      <StrictMode>
        <Probe />
      </StrictMode>,
    );

    await waitFor(() => expect(screen.getByTestId('phase').textContent).toBe('ok'), { timeout: 5000 });
    // StrictMode double-invokes the effect, but the disposed engine's pending debounce is cancelled,
    // so exactly one execution reaches the API for this edit.
    expect(execute.mock.calls.length).toBe(1);
  });
});
