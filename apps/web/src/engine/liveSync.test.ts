/**
 * Live-sync guarantees: debounce, monotonic revisions, stale-response rejection, in-flight abort,
 * stale values cleared on error. These tests use fake timers and hand-resolved promises.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Graph } from '@lng/graph-contract';
import { LiveSyncEngine, type LiveError, type LiveResult } from './liveSync';

const graph = (value: number): Graph => ({
  name: 'test',
  nodes: [{ id: 'n1', type: 'number', position: { x: 0, y: 0 }, data: { value } }],
  edges: [],
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function result(revision: number, value = 1): LiveResult {
  return {
    revision,
    status: 'ok',
    results: [
      {
        nodeId: 'n1',
        status: 'ok',
        value,
        errorCode: null,
        message: null,
        net: 'n1',
        issues: [],
        warnings: [],
        inputStates: {},
      },
    ],
    warnings: [],
    netlist: 'V1 n1 0 DC 1\n.op\n.end\n',
    ngspiceVersion: 'ngspice-44.2',
    timings: { validationMs: 0, netlistMs: 0, ngspiceMs: 1, parseMs: 0, totalMs: 1 },
    topology: {},
  };
}

describe('LiveSyncEngine', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('debounces rapid edits into a single execution', async () => {
    const execute = vi.fn(async (_graph: Graph, revision: number) => result(revision));
    const engine = new LiveSyncEngine({ execute, debounceMs: 300 });

    engine.update(graph(1));
    vi.advanceTimersByTime(100);
    engine.update(graph(2));
    vi.advanceTimersByTime(100);
    engine.update(graph(3));
    expect(execute).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(300);
    expect(execute).toHaveBeenCalledTimes(1);
    expect(engine.getSnapshot().runs).toBe(1);
    expect(engine.getSnapshot().revision).toBe(1);
    engine.dispose();
  });

  it('increments revisions monotonically', async () => {
    const seen: number[] = [];
    const engine = new LiveSyncEngine({
      debounceMs: 10,
      execute: async (_graph, revision) => {
        seen.push(revision);
        return result(revision);
      },
    });
    engine.update(graph(1));
    await vi.advanceTimersByTimeAsync(20);
    engine.update(graph(2));
    await vi.advanceTimersByTimeAsync(20);
    engine.update(graph(3));
    await vi.advanceTimersByTimeAsync(20);
    expect(seen).toEqual([1, 2, 3]);
    expect(engine.getSnapshot().revision).toBe(3);
    engine.dispose();
  });

  it('drops a stale response that arrives after a newer edit', async () => {
    const first = deferred<LiveResult>();
    const second = deferred<LiveResult>();
    const execute = vi
      .fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    const engine = new LiveSyncEngine({ execute, debounceMs: 10 });

    engine.update(graph(1));
    await vi.advanceTimersByTimeAsync(20);
    engine.update(graph(2));
    await vi.advanceTimersByTimeAsync(20);
    expect(execute).toHaveBeenCalledTimes(2);

    // the older run answers last: it must be ignored
    second.resolve(result(2, 20));
    await vi.advanceTimersByTimeAsync(0);
    first.resolve(result(1, 10));
    await vi.advanceTimersByTimeAsync(0);

    const snapshot = engine.getSnapshot();
    expect(snapshot.revision).toBe(2);
    expect(snapshot.result?.results[0]?.value).toBe(20);
    engine.dispose();
  });

  it('aborts the in-flight request when a new edit arrives', async () => {
    const signals: AbortSignal[] = [];
    const pending: ReturnType<typeof deferred<LiveResult>>[] = [];
    const execute = vi.fn((_graph: Graph, _revision: number, signal: AbortSignal) => {
      signals.push(signal);
      const flight = deferred<LiveResult>();
      pending.push(flight);
      return flight.promise; // stays in flight until the test resolves it
    });
    const engine = new LiveSyncEngine({ execute, debounceMs: 10 });

    engine.update(graph(1));
    await vi.advanceTimersByTimeAsync(20);
    expect(signals).toHaveLength(1);
    expect(signals[0]?.aborted).toBe(false);

    engine.update(graph(2));
    expect(signals[0]?.aborted).toBe(true); // cancelled as soon as the edit lands

    await vi.advanceTimersByTimeAsync(20);
    expect(signals).toHaveLength(2);

    // the aborted request finally answers: it must not resurrect its (older) result
    pending[0]!.resolve(result(1, 10));
    await vi.advanceTimersByTimeAsync(0);
    expect(engine.getSnapshot().phase).toBe('running');

    pending[1]!.resolve(result(2, 20));
    await vi.advanceTimersByTimeAsync(0);
    expect(engine.getSnapshot().result?.results[0]?.value).toBe(20);
    engine.dispose();
  });

  it('does not publish an error when a request is superseded and rejects with AbortError', async () => {
    const pending: { reject: (reason?: unknown) => void }[] = [];
    const execute = vi.fn(
      (_graph: Graph, _revision: number, _signal: AbortSignal) =>
        new Promise<LiveResult>((_resolve, reject) => {
          pending.push({ reject });
        }),
    );
    const engine = new LiveSyncEngine({ execute, debounceMs: 10 });

    engine.update(graph(1));
    await vi.advanceTimersByTimeAsync(20);
    engine.update(graph(2));

    pending[0]!.reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    await vi.advanceTimersByTimeAsync(20);

    const snapshot = engine.getSnapshot();
    expect(snapshot.phase).toBe('running'); // the newer run is still in flight
    expect(snapshot.error).toBeNull();
    engine.dispose();
  });

  it('marks values stale immediately on edit, before the next run', async () => {
    const engine = new LiveSyncEngine({ execute: async (_graph, revision) => result(revision), debounceMs: 50 });
    engine.update(graph(1));
    await vi.advanceTimersByTimeAsync(60);
    expect(engine.getSnapshot().phase).toBe('ok');

    engine.update(graph(2));
    const snapshot = engine.getSnapshot();
    expect(snapshot.phase).toBe('stale');
    expect(snapshot.result?.revision).toBe(1); // still the old payload, but flagged stale
    engine.dispose();
  });

  it('clears results and exposes structured errors when the backend fails', async () => {
    const failure: LiveError = {
      code: 'SIMULATION_SERVICE_UNAVAILABLE',
      message: 'simulation service is unavailable',
      nodeIds: ['n1'],
      edgeIds: [],
      status: 503,
    };
    const engine = new LiveSyncEngine({
      execute: async () => {
        throw Object.assign(new Error(failure.message), failure);
      },
      debounceMs: 10,
    });

    engine.update(graph(1));
    await vi.advanceTimersByTimeAsync(20);

    const snapshot = engine.getSnapshot();
    expect(snapshot.phase).toBe('error');
    expect(snapshot.result).toBeNull(); // no stale values are kept after an error
    expect(snapshot.error?.code).toBe('SIMULATION_SERVICE_UNAVAILABLE');
    expect(snapshot.error?.nodeIds).toEqual(['n1']);
    expect(snapshot.inFlight).toBe(false);
    engine.dispose();
  });

  it('recovers on retry after an error', async () => {
    let attempts = 0;
    const engine = new LiveSyncEngine({
      debounceMs: 10,
      execute: async (_graph, revision) => {
        attempts += 1;
        if (attempts === 1) throw Object.assign(new Error('down'), { code: 'SIMULATION_SERVICE_UNAVAILABLE' });
        return result(revision, 7);
      },
    });

    engine.update(graph(1));
    await vi.advanceTimersByTimeAsync(20);
    expect(engine.getSnapshot().phase).toBe('error');

    engine.retry();
    await vi.advanceTimersByTimeAsync(20);
    expect(engine.getSnapshot().phase).toBe('ok');
    expect(engine.getSnapshot().result?.results[0]?.value).toBe(7);
    engine.dispose();
  });

  it('does not execute after dispose', async () => {
    const execute = vi.fn(async (_graph: Graph, revision: number) => result(revision));
    const engine = new LiveSyncEngine({ execute, debounceMs: 10 });
    engine.update(graph(1));
    engine.dispose();
    await vi.advanceTimersByTimeAsync(50);
    expect(execute).not.toHaveBeenCalled();
  });
});
