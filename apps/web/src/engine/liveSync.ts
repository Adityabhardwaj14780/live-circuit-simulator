/**
 * Live synchronisation engine.
 *
 * There is no Run button: every graph edit is pushed through this engine, which
 *
 *   1. debounces edits (default 300 ms) so dragging a node does not fire a simulation per pixel,
 *   2. assigns a monotonically increasing local revision to every execution,
 *   3. aborts the in-flight request when a newer edit arrives,
 *   4. drops any response whose revision is not the latest one (stale-result guard),
 *   5. publishes a `stale` state immediately on edit so old values are never shown as current,
 *   6. surfaces structured backend errors (code + node ids) so the UI can highlight the offenders.
 */
import type { Graph } from '@lng/graph-contract';

export type LivePhase = 'idle' | 'stale' | 'running' | 'ok' | 'partial' | 'error';

export interface LiveNodeResult {
  nodeId: string;
  status: 'ok' | 'error' | 'blocked';
  value: number | null;
  errorCode: string | null;
  message: string | null;
  net: string | null;
  issues: { code: string; message: string; severity: string; handle?: string | null }[];
  warnings: string[];
  inputStates: Record<string, string>;
}

export interface LiveWarning {
  code: string;
  message: string;
  nodeIds: string[];
  edgeIds: string[];
}

export interface LiveResult {
  revision: number;
  status: 'ok' | 'partial' | 'error';
  results: LiveNodeResult[];
  warnings: LiveWarning[];
  netlist: string | null;
  ngspiceVersion: string | null;
  timings: { validationMs: number; netlistMs: number; ngspiceMs: number; parseMs: number; totalMs: number };
  topology: { order?: string[]; cyclic?: string[]; blocked?: string[] };
}

export interface LiveError {
  code: string;
  message: string;
  nodeIds: string[];
  edgeIds: string[];
  status?: number;
}

export interface LiveSnapshot {
  phase: LivePhase;
  /** revision the currently displayed values belong to (0 = none) */
  revision: number;
  /** revision most recently requested */
  requestedRevision: number;
  result: LiveResult | null;
  error: LiveError | null;
  inFlight: boolean;
  lastDurationMs: number | null;
  runs: number;
}

/** Timer handles differ between DOM (`number`) and Node (`Timeout`) typings — keep them opaque. */
export type TimerHandle = ReturnType<typeof setTimeout>;

export interface LiveSyncOptions {
  execute: (graph: Graph, revision: number, signal: AbortSignal) => Promise<LiveResult>;
  persist?: (graph: Graph) => void;
  debounceMs?: number;
  /** injected in tests to avoid real timers */
  setTimeoutFn?: (handler: () => void, timeout: number) => TimerHandle;
  clearTimeoutFn?: (handle: TimerHandle) => void;
  now?: () => number;
}

interface ResolvedOptions {
  execute: (graph: Graph, revision: number, signal: AbortSignal) => Promise<LiveResult>;
  persist?: (graph: Graph) => void;
  debounceMs: number;
  setTimeoutFn: (handler: () => void, timeout: number) => TimerHandle;
  clearTimeoutFn: (handle: TimerHandle) => void;
  now: () => number;
}

export type LiveListener = (snapshot: LiveSnapshot) => void;

const EMPTY_SNAPSHOT: LiveSnapshot = {
  phase: 'idle',
  revision: 0,
  requestedRevision: 0,
  result: null,
  error: null,
  inFlight: false,
  lastDurationMs: null,
  runs: 0,
};

export class LiveSyncEngine {
  private readonly options: ResolvedOptions;

  private snapshot: LiveSnapshot = { ...EMPTY_SNAPSHOT };
  private listeners = new Set<LiveListener>();
  private timer: TimerHandle | null = null;
  private controller: AbortController | null = null;
  private latestGraph: Graph | null = null;
  private localRevision = 0;
  private runs = 0;
  private disposed = false;

  constructor(options: LiveSyncOptions) {
    this.options = {
      debounceMs: options.debounceMs ?? 300,
      execute: options.execute,
      persist: options.persist,
      // Wrapped, not passed by reference: calling the native timer detached from `window` throws
      // "Illegal invocation" in browsers (fine in Node, so this is caught by the browser E2E test).
      setTimeoutFn: options.setTimeoutFn ?? ((handler: () => void, timeout: number) => setTimeout(handler, timeout)),
      clearTimeoutFn: options.clearTimeoutFn ?? ((handle: TimerHandle) => clearTimeout(handle)),
      now: options.now ?? (() => Date.now()),
    };
  }

  subscribe(listener: LiveListener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot);
    return () => this.listeners.delete(listener);
  }

  getSnapshot(): LiveSnapshot {
    return this.snapshot;
  }

  /** Called on every graph edit. Immediately invalidates displayed values (no stale readings). */
  update(graph: Graph, options: { immediate?: boolean } = {}): void {
    if (this.disposed) return;
    this.latestGraph = graph;
    this.options.persist?.(graph);

    if (this.snapshot.revision > 0 || this.snapshot.result) {
      this.publish({
        phase: this.snapshot.inFlight ? 'running' : 'stale',
        result: this.snapshot.result,
        error: null,
      });
    }

    // cancel any in-flight request right away: its answer can no longer be the newest one
    this.controller?.abort();
    this.controller = null;

    if (this.timer !== null) this.options.clearTimeoutFn(this.timer);
    if (options.immediate) {
      void this.flush();
      return;
    }
    this.timer = this.options.setTimeoutFn(() => {
      this.timer = null;
      void this.flush();
    }, this.options.debounceMs);
  }

  /** Manual retry (used by the "Retry" action when the simulation service was unavailable). */
  retry(): void {
    if (this.disposed) return;
    void this.flush();
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer !== null) this.options.clearTimeoutFn(this.timer);
    this.controller?.abort();
    this.listeners.clear();
  }

  private publish(patch: Partial<LiveSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener(this.snapshot);
  }

  private async flush(): Promise<void> {
    if (this.disposed || !this.latestGraph) return;

    const graph = this.latestGraph;
    const revision = ++this.localRevision;
    const startedAt = this.options.now();

    // cancel the previous request: its answer can no longer be the newest one
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;

    this.publish({
      phase: 'running',
      requestedRevision: revision,
      inFlight: true,
      error: null,
    });

    try {
      const result = await this.options.execute(graph, revision, controller.signal);
      if (this.disposed) return;
      if (revision !== this.localRevision) return; // a newer edit already superseded this run
      this.runs += 1;
      this.publish({
        phase: result.status,
        revision: result.revision,
        result,
        error: null,
        inFlight: false,
        lastDurationMs: this.options.now() - startedAt,
        runs: this.runs,
      });
    } catch (error) {
      if (this.disposed) return;
      if (revision !== this.localRevision) return; // superseded: ignore, do not clear newer results
      const aborted = (error as { name?: string })?.name === 'AbortError';
      if (aborted) return;
      this.runs += 1;
      this.publish({
        phase: 'error',
        revision: 0,
        result: null, // values from an older revision are never kept on error
        error: toLiveError(error),
        inFlight: false,
        lastDurationMs: this.options.now() - startedAt,
        runs: this.runs,
      });
    } finally {
      if (this.controller === controller) this.controller = null;
    }
  }
}

function toLiveError(error: unknown): LiveError {
  const candidate = error as Partial<LiveError> & { message?: string };
  return {
    code: candidate?.code ?? 'UNKNOWN_ERROR',
    message: candidate?.message ?? String(error),
    nodeIds: candidate?.nodeIds ?? [],
    edgeIds: candidate?.edgeIds ?? [],
    status: candidate?.status,
  };
}
