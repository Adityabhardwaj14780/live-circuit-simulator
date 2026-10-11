import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { EXAMPLE_GRAPH, NODE_TYPES, type Graph, type NodeType } from '@lng/graph-contract';
import { api, type HealthResponse } from './api/client';
import { Inspector } from './components/Inspector';
import { Palette } from './components/Palette';
import { StatusBar } from './components/StatusBar';
import { CustomNode, defaultDataFor } from './graph/nodeDefs';
import { resultByNodeId, useLiveGraph } from './hooks/useLiveGraph';
import { findInvalidEdges, validateConnection } from './engine/validation';

const nodeTypes = { number: CustomNode, add: CustomNode, multiply: CustomNode, divide: CustomNode, formula: CustomNode, display: CustomNode };

let nodeCounter = 0;
const nextNodeId = (type: NodeType): string => `${type}_${Date.now().toString(36)}_${nodeCounter++}`;

function toFlowNodes(graph: Graph): Node[] {
  return graph.nodes.map((node) => ({
    id: node.id,
    type: node.type,
    position: node.position,
    data: { ...node.data },
  }));
}

function toFlowEdges(graph: Graph): Edge[] {
  return graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.sourceHandle,
    targetHandle: edge.targetHandle,
    animated: false,
  }));
}

function Editor() {
  const wrapper = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition } = useReactFlow();

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [graphName] = useState('live graph');
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [graphRevision, setGraphRevision] = useState<number | null>(null);
  const [persistedAt, setPersistedAt] = useState<string | null>(null);
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [loadError, setLoadError] = useState<string | null>(null);

  const { snapshot, retry } = useLiveGraph({
    nodes,
    edges,
    graphName,
    onPersisted: (revision, updatedAt) => {
      setGraphRevision(revision);
      setPersistedAt(updatedAt);
    },
  });

  // ---- load persisted graph + health on start -------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const stored = await api.loadGraph();
        if (cancelled) return;
        if (stored.graph.nodes.length > 0) {
          setNodes(toFlowNodes(stored.graph));
          setEdges(toFlowEdges(stored.graph));
          setGraphRevision(stored.revision);
          setPersistedAt(stored.updatedAt);
        }
        setLoadState('ready');
      } catch (error) {
        if (cancelled) return;
        setLoadState('failed');
        setLoadError((error as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setEdges, setNodes]);

  useEffect(() => {
    const load = () => void api.health().then(setHealth).catch(() => setHealth(null));
    load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, []);

  // ---- results are merged into node data (and cleared when a run is stale/failed) ------------
  const results = useMemo(() => resultByNodeId(snapshot.result), [snapshot.result]);
  const decoratedNodes = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        data: {
          ...node.data,
          result: snapshot.phase === 'ok' || snapshot.phase === 'partial' ? results[node.id] ?? null : null,
          stale: snapshot.phase === 'stale' || snapshot.phase === 'running',
        },
      })),
    [nodes, results, snapshot.phase],
  );

  // ---- graph editing --------------------------------------------------------------------------
  const addNode = useCallback(
    (type: NodeType, position?: { x: number; y: number }) => {
      const at = position ?? { x: 120 + Math.random() * 80, y: 120 + Math.random() * 80 };
      setNodes((current) => [
        ...current,
        { id: nextNodeId(type), type, position: at, data: defaultDataFor(type) },
      ]);
    },
    [setNodes],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((current) => {
        const verdict = validateConnection(nodes, current, {
          source: connection.source,
          target: connection.target,
          sourceHandle: connection.sourceHandle ?? null,
          targetHandle: connection.targetHandle ?? null,
        });
        if (!verdict.ok) return current; // invalid connection is refused, the canvas stays as it was
        return addEdge({ ...connection, id: `e_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}` }, current);
      });
    },
    [nodes, setEdges],
  );

  const isValidConnection = useCallback(
    (connection: Connection | Edge) =>
      validateConnection(nodes, edges, {
        source: connection.source,
        target: connection.target,
        sourceHandle: connection.sourceHandle ?? null,
        targetHandle: connection.targetHandle ?? null,
      }).ok,
    [nodes, edges],
  );

  const invalidEdges = useMemo(() => findInvalidEdges(nodes, edges), [nodes, edges]);

  const onDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      const type = event.dataTransfer.getData('application/lng-node-type') as NodeType;
      if (!NODE_TYPES.includes(type)) return;
      addNode(type, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
    },
    [addNode, screenToFlowPosition],
  );

  const loadExample = useCallback(() => {
    setNodes(toFlowNodes(EXAMPLE_GRAPH));
    setEdges(toFlowEdges(EXAMPLE_GRAPH));
  }, [setEdges, setNodes]);

  const clearGraph = useCallback(() => {
    setNodes([]);
    setEdges([]);
  }, [setEdges, setNodes]);

  return (
    <div className="layout">
      <header className="topbar">
        <h1>Live Node Graph Engine</h1>
        <span className="muted">
          React Flow canvas → Express API → FastAPI + ngspice. Edits re-simulate automatically; there is no Run button.
        </span>
        <div className="topbar__actions">
          <button type="button" onClick={loadExample} data-testid="load-example">
            Load example
          </button>
          <button type="button" onClick={clearGraph} data-testid="clear-graph">
            Clear
          </button>
          <button type="button" onClick={retry} data-testid="retry">
            Retry simulation
          </button>
        </div>
      </header>

      <div className="workspace">
        <Palette onAdd={(type) => addNode(type)} />

        <main className="canvas" ref={wrapper} onDrop={onDrop} onDragOver={(event) => event.preventDefault()} data-testid="canvas">
          {loadState === 'failed' && (
            <div className="banner banner--error">
              <strong>could not load the saved graph</strong>
              <span>{loadError}</span>
            </div>
          )}
          {invalidEdges.length > 0 && (
            <div className="banner banner--warn" data-testid="invalid-edges">
              {invalidEdges.length} invalid edge(s) in this graph:{' '}
              {invalidEdges.map((problem) => `${problem.edgeId} (${problem.code})`).join(', ')}
            </div>
          )}
          <ReactFlow
            nodes={decoratedNodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            isValidConnection={isValidConnection}
            onDrop={onDrop}
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={18} />
            <Controls />
            <MiniMap pannable zoomable />
          </ReactFlow>
        </main>

        <Inspector snapshot={snapshot} />
      </div>

      <StatusBar snapshot={snapshot} health={health} graphRevision={graphRevision} persistedAt={persistedAt} />
    </div>
  );
}

export default function App() {
  return (
    <ReactFlowProvider>
      <Editor />
    </ReactFlowProvider>
  );
}
