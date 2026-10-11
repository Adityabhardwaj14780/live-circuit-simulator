import { describe, expect, it } from 'vitest';
import {
  analyzeTopology,
  checkConnection,
  defaultDataForType,
  isKnownHandle,
  validateGraphStructure,
  EXAMPLE_GRAPH,
  LIMITS,
  NODE_SPECS,
  NODE_TYPES,
  type Graph,
} from '../src/index';

const base = (over: Partial<Graph> = {}): Graph => ({ nodes: [], edges: [], ...over });

describe('node registry', () => {
  it('declares exactly the six required node types with named handles', () => {
    expect(NODE_TYPES.sort()).toEqual(['add', 'display', 'divide', 'formula', 'multiply', 'number']);
    expect(NODE_SPECS.number.outputs).toEqual(['out']);
    expect(NODE_SPECS.add.inputs).toEqual(['a', 'b']);
    expect(NODE_SPECS.multiply.inputs).toEqual(['a', 'b']);
    expect(NODE_SPECS.divide.inputs).toEqual(['a', 'b']);
    expect(NODE_SPECS.formula.inputs).toEqual(['a', 'b']);
    expect(NODE_SPECS.display.inputs).toEqual(['in']);
    expect(NODE_SPECS.display.outputs).toEqual([]);
  });

  it('knows which handles exist', () => {
    expect(isKnownHandle('add', 'input', 'a')).toBe(true);
    expect(isKnownHandle('add', 'input', 'out')).toBe(false);
    expect(isKnownHandle('display', 'output', 'out')).toBe(false);
    expect(isKnownHandle('nope', 'input', 'a')).toBe(false);
  });

  it('provides defaults for editable data', () => {
    expect(defaultDataForType('number')).toEqual({ value: 1 });
    expect(defaultDataForType('formula')).toEqual({ expression: 'a + b' });
    expect(defaultDataForType('add')).toEqual({});
  });
});

describe('structural validation', () => {
  it('accepts the example graph', () => {
    expect(validateGraphStructure(EXAMPLE_GRAPH)).toEqual([]);
  });

  it('rejects self connections', () => {
    const graph = base({
      nodes: [
        { id: 'x', type: 'add', position: { x: 0, y: 0 }, data: {} },
        { id: 'y', type: 'number', position: { x: 0, y: 0 }, data: { value: 1 } },
      ],
      edges: [{ id: 'e1', source: 'x', sourceHandle: 'out', target: 'x', targetHandle: 'a' }],
    });
    expect(validateGraphStructure(graph).map((i) => i.code)).toContain('SELF_LOOP');
  });

  it('rejects unknown handles (e.g. feeding a number node)', () => {
    const graph = base({
      nodes: [
        { id: 'a', type: 'number', position: { x: 0, y: 0 }, data: { value: 1 } },
        { id: 'b', type: 'number', position: { x: 0, y: 0 }, data: { value: 1 } },
      ],
      edges: [{ id: 'e1', source: 'a', sourceHandle: 'out', target: 'b', targetHandle: 'in' }],
    });
    expect(validateGraphStructure(graph).map((i) => i.code)).toContain('UNKNOWN_HANDLE');
  });

  it('rejects two edges into the same input and keeps reporting missing inputs', () => {
    const graph = base({
      nodes: [
        { id: 'a', type: 'number', position: { x: 0, y: 0 }, data: { value: 1 } },
        { id: 'b', type: 'number', position: { x: 0, y: 0 }, data: { value: 2 } },
        { id: 'c', type: 'add', position: { x: 0, y: 0 }, data: {} },
      ],
      edges: [
        { id: 'e1', source: 'a', sourceHandle: 'out', target: 'c', targetHandle: 'a' },
        { id: 'e2', source: 'b', sourceHandle: 'out', target: 'c', targetHandle: 'a' },
      ],
    });
    const codes = validateGraphStructure(graph).map((i) => i.code);
    expect(codes).toContain('DUPLICATE_INPUT_EDGE');
    expect(codes).toContain('MISSING_INPUT'); // input b is unwired
  });

  it('allows one output to feed many inputs', () => {
    const graph = base({
      nodes: [
        { id: 'a', type: 'number', position: { x: 0, y: 0 }, data: { value: 5 } },
        { id: 's1', type: 'add', position: { x: 0, y: 0 }, data: {} },
        { id: 's2', type: 'add', position: { x: 0, y: 0 }, data: {} },
      ],
      edges: [
        { id: 'e1', source: 'a', sourceHandle: 'out', target: 's1', targetHandle: 'a' },
        { id: 'e2', source: 'a', sourceHandle: 'out', target: 's1', targetHandle: 'b' },
        { id: 'e3', source: 'a', sourceHandle: 'out', target: 's2', targetHandle: 'a' },
        { id: 'e4', source: 'a', sourceHandle: 'out', target: 's2', targetHandle: 'b' },
      ],
    });
    expect(validateGraphStructure(graph)).toEqual([]);
  });

  it('rejects unknown node types and bad numeric data', () => {
    const graph = base({
      nodes: [
        { id: 'x', type: 'multiplier' as never, position: { x: 0, y: 0 }, data: {} },
        { id: 'y', type: 'number', position: { x: 0, y: 0 }, data: { value: 'abc' } },
      ],
    });
    const codes = validateGraphStructure(graph).map((i) => i.code);
    expect(codes).toContain('UNKNOWN_NODE_TYPE');
    expect(codes).toContain('NON_NUMERIC_VALUE');
  });

  it('enforces size limits', () => {
    const nodes = Array.from({ length: LIMITS.maxNodes + 1 }, (_, i) => ({
      id: `n${i}`,
      type: 'number' as const,
      position: { x: 0, y: 0 },
      data: { value: i },
    }));
    expect(validateGraphStructure(base({ nodes })).map((i) => i.code)).toContain('GRAPH_SCHEMA_INVALID');
  });
});

describe('topology / cycle detection', () => {
  it('produces a deterministic order and finds no cycles in a DAG', () => {
    const topo = analyzeTopology(EXAMPLE_GRAPH);
    expect(topo.cyclic).toEqual([]);
    expect(topo.blocked).toEqual([]);
    expect(topo.order[0]).toBe('n1');
    expect(topo.order.indexOf('n3')).toBeLessThan(topo.order.indexOf('n5'));
    expect(topo.order.indexOf('n5')).toBeLessThan(topo.order.indexOf('n7'));
  });

  it('detects a direct two-node cycle', () => {
    const graph = base({
      nodes: [
        { id: 'a', type: 'add', position: { x: 0, y: 0 }, data: {} },
        { id: 'b', type: 'add', position: { x: 0, y: 0 }, data: {} },
      ],
      edges: [
        { id: 'e1', source: 'a', sourceHandle: 'out', target: 'b', targetHandle: 'a' },
        { id: 'e2', source: 'b', sourceHandle: 'out', target: 'a', targetHandle: 'a' },
      ],
    });
    const topo = analyzeTopology(graph);
    expect(topo.cyclic.sort()).toEqual(['a', 'b']);
    expect(topo.order).toEqual([]);
  });

  it('detects a longer cycle and blocks descendants', () => {
    const graph = base({
      nodes: [
        { id: 'a', type: 'add', position: { x: 0, y: 0 }, data: {} },
        { id: 'b', type: 'add', position: { x: 0, y: 0 }, data: {} },
        { id: 'c', type: 'multiply', position: { x: 0, y: 0 }, data: {} },
        { id: 'd', type: 'display', position: { x: 0, y: 0 }, data: {} },
      ],
      edges: [
        { id: 'e1', source: 'a', sourceHandle: 'out', target: 'b', targetHandle: 'a' },
        { id: 'e2', source: 'b', sourceHandle: 'out', target: 'c', targetHandle: 'a' },
        { id: 'e3', source: 'c', sourceHandle: 'out', target: 'a', targetHandle: 'a' },
        { id: 'e4', source: 'c', sourceHandle: 'out', target: 'd', targetHandle: 'in' },
      ],
    });
    const topo = analyzeTopology(graph);
    expect(topo.cyclic.sort()).toEqual(['a', 'b', 'c']);
    expect(topo.blocked).toEqual(['d']);
  });

  it('handles diamonds without false positives', () => {
    const graph = base({
      nodes: [
        { id: 'a', type: 'number', position: { x: 0, y: 0 }, data: { value: 1 } },
        { id: 'b', type: 'add', position: { x: 0, y: 0 }, data: {} },
        { id: 'c', type: 'add', position: { x: 0, y: 0 }, data: {} },
        { id: 'd', type: 'multiply', position: { x: 0, y: 0 }, data: {} },
      ],
      edges: [
        { id: 'e1', source: 'a', sourceHandle: 'out', target: 'b', targetHandle: 'a' },
        { id: 'e2', source: 'a', sourceHandle: 'out', target: 'c', targetHandle: 'a' },
        { id: 'e3', source: 'b', sourceHandle: 'out', target: 'd', targetHandle: 'a' },
        { id: 'e4', source: 'c', sourceHandle: 'out', target: 'd', targetHandle: 'b' },
      ],
    });
    const topo = analyzeTopology(graph);
    expect(topo.cyclic).toEqual([]);
    expect(topo.order).toEqual(['a', 'b', 'c', 'd']);
  });
});

describe('connection guard', () => {
  const graph: Graph = {
    nodes: [
      { id: 'a', type: 'number', position: { x: 0, y: 0 }, data: { value: 1 } },
      { id: 'b', type: 'add', position: { x: 0, y: 0 }, data: {} },
    ],
    edges: [{ id: 'e1', source: 'a', sourceHandle: 'out', target: 'b', targetHandle: 'a' }],
  };

  it('accepts an empty input handle', () => {
    expect(checkConnection(graph, { source: 'a', target: 'b', sourceHandle: 'out', targetHandle: 'b' })).toEqual({ ok: true });
  });

  it('rejects occupied inputs, self loops and unknown handles', () => {
    expect(
      checkConnection(graph, { source: 'a', target: 'b', sourceHandle: 'out', targetHandle: 'a' }).code,
    ).toBe('DUPLICATE_INPUT_EDGE');
    expect(
      checkConnection(graph, { source: 'b', target: 'b', sourceHandle: 'out', targetHandle: 'a' }).code,
    ).toBe('SELF_LOOP');
    expect(
      checkConnection(graph, { source: 'a', target: 'b', sourceHandle: 'value', targetHandle: 'a' }).code,
    ).toBe('UNKNOWN_HANDLE');
  });
});
