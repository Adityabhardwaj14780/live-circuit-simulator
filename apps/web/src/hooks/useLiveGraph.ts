/**
 * React glue around LiveSyncEngine: watches the canvas, executes on every edit and merges results
 * back into node data. No Run button — editing is the trigger.
 *
 * The engine is created *inside* the effect (and torn down in its cleanup) rather than in a ref
 * initialiser. React StrictMode mounts, unmounts and re-mounts effects in development, so a
 * lazily-initialised ref would be disposed on the first cleanup and then re-subscribed as null,
 * crashing the editor in `npm run dev` while working in a production build. Creating it per effect
 * run keeps both modes identical.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Edge, Node } from '@xyflow/react';
import type { Graph } from '@lng/graph-contract';
import { api } from '../api/client';
import { LiveSyncEngine, type LiveResult, type LiveSnapshot } from '../engine/liveSync';

export function toGraphPayload(nodes: Node[], edges: Edge[], name: string): Graph {
  return {
    name,
    nodes: nodes.map((node) => ({
      id: node.id,
      type: String(node.type ?? 'display') as Graph['nodes'][number]['type'],
      position: { x: Math.round(node.position.x), y: Math.round(node.position.y) },
      data: serializeData(node),
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: String(edge.sourceHandle ?? ''),
      targetHandle: String(edge.targetHandle ?? ''),
    })),
  };
}

function serializeData(node: Node): Record<string, unknown> {
  const data = (node.data ?? {}) as Record<string, unknown>;
  const payload: Record<string, unknown> = {};
  if (typeof data.value === 'number') payload.value = data.value;
  if (typeof data.expression === 'string') payload.expression = data.expression;
  return payload;
}

/** Only structural/serializable fields matter for the change signature. */
function graphSignature(graph: Graph): string {
  return JSON.stringify({ nodes: graph.nodes, edges: graph.edges });
}

interface Callbacks {
  onPersisted?: (revision: number, updatedAt: string) => void;
}

export interface UseLiveGraphOptions {
  nodes: Node[];
  edges: Edge[];
  graphName: string;
  persistDebounceMs?: number;
  onPersisted?: (revision: number, updatedAt: string) => void;
}

export function useLiveGraph({ nodes, edges, graphName, persistDebounceMs = 900, onPersisted }: UseLiveGraphOptions) {
  const [snapshot, setSnapshot] = useState<LiveSnapshot>({
    phase: 'idle',
    revision: 0,
    requestedRevision: 0,
    result: null,
    error: null,
    inFlight: false,
    lastDurationMs: null,
    runs: 0,
  });

  const engineRef = useRef<LiveSyncEngine | null>(null);
  const persistTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const graphRef = useRef<Graph | null>(null);
  // keeps the engine's persist callback reading the latest props without re-creating the engine
  const callbacksRef = useRef<Callbacks>({ onPersisted });
  callbacksRef.current = { onPersisted };

  useEffect(() => {
    const engine = new LiveSyncEngine({
      execute: (graph, revision, signal) => api.execute(graph, revision, signal) as Promise<LiveResult>,
      persist: (graph) => {
        graphRef.current = graph;
        if (persistTimer.current) clearTimeout(persistTimer.current);
        persistTimer.current = setTimeout(() => {
          const payload = graphRef.current;
          if (!payload) return;
          void api
            .saveGraph(payload)
            .then((stored) => callbacksRef.current.onPersisted?.(stored.revision, stored.updatedAt))
            .catch(() => {
              /* persistence problems must never break editing; /health surfaces them */
            });
        }, persistDebounceMs);
      },
    });
    engineRef.current = engine;
    const unsubscribe = engine.subscribe(setSnapshot);

    return () => {
      unsubscribe();
      engine.dispose();
      engineRef.current = null;
      if (persistTimer.current) clearTimeout(persistTimer.current);
    };
  }, [persistDebounceMs]);

  const graph = useMemo(() => toGraphPayload(nodes, edges, graphName), [nodes, edges, graphName]);
  const signature = useMemo(() => graphSignature(graph), [graph]);

  useEffect(() => {
    engineRef.current?.update(graph);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  const retry = useCallback(() => engineRef.current?.retry(), []);

  return { snapshot, retry };
}

export function resultByNodeId(result: LiveResult | null): Record<string, LiveResult['results'][number]> {
  const map: Record<string, LiveResult['results'][number]> = {};
  for (const entry of result?.results ?? []) map[entry.nodeId] = entry;
  return map;
}
