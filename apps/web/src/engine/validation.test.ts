import { describe, expect, it } from 'vitest';
import type { Edge, Node } from '@xyflow/react';
import { findInvalidEdges, reaches, validateConnection } from './validation';

const nodes: Node[] = [
  { id: 'n1', type: 'number', position: { x: 0, y: 0 }, data: { value: 2 } },
  { id: 'n2', type: 'number', position: { x: 0, y: 60 }, data: { value: 3 } },
  { id: 'a1', type: 'add', position: { x: 200, y: 0 }, data: {} },
  { id: 'm1', type: 'multiply', position: { x: 400, y: 0 }, data: {} },
  { id: 'd1', type: 'display', position: { x: 600, y: 0 }, data: {} },
];

describe('validateConnection', () => {
  it('accepts a valid edge and allows fan-out from one output', () => {
    const edges: Edge[] = [
      { id: 'e1', source: 'n1', target: 'a1', sourceHandle: 'out', targetHandle: 'a' },
      { id: 'e2', source: 'n1', target: 'a1', sourceHandle: 'out', targetHandle: 'b' },
    ];
    const first = validateConnection(nodes, [], { source: 'n1', target: 'm1', sourceHandle: 'out', targetHandle: 'a' });
    expect(first.ok).toBe(true);
    const second = validateConnection(nodes, edges, { source: 'n1', target: 'm1', sourceHandle: 'out', targetHandle: 'b' });
    expect(second.ok).toBe(true);
  });

  it('rejects a second edge into the same input', () => {
    const edges: Edge[] = [{ id: 'e1', source: 'n1', target: 'a1', sourceHandle: 'out', targetHandle: 'a' }];
    const verdict = validateConnection(nodes, edges, { source: 'n2', target: 'a1', sourceHandle: 'out', targetHandle: 'a' });
    expect(verdict.ok).toBe(false);
    expect(verdict.code).toBe('DUPLICATE_INPUT_EDGE');
  });

  it('rejects self-connections and unknown handles', () => {
    expect(validateConnection(nodes, [], { source: 'a1', target: 'a1', sourceHandle: 'out', targetHandle: 'a' }).code).toBe('SELF_LOOP');
    expect(validateConnection(nodes, [], { source: 'n1', target: 'a1', sourceHandle: 'value', targetHandle: 'a' }).code).toBe(
      'UNKNOWN_HANDLE',
    );
    expect(validateConnection(nodes, [], { source: 'a1', target: 'd1', sourceHandle: 'out', targetHandle: 'out' }).code).toBe(
      'UNKNOWN_HANDLE',
    );
    expect(validateConnection(nodes, [], { source: 'd1', target: 'a1', sourceHandle: 'in', targetHandle: 'a' }).code).toBe(
      'UNKNOWN_HANDLE',
    );
  });

  it('rejects an edge that would close a feedback loop', () => {
    const edges: Edge[] = [
      { id: 'e1', source: 'a1', target: 'm1', sourceHandle: 'out', targetHandle: 'a' },
      { id: 'e2', source: 'n1', target: 'm1', sourceHandle: 'out', targetHandle: 'b' },
    ];
    const verdict = validateConnection(nodes, edges, { source: 'm1', target: 'a1', sourceHandle: 'out', targetHandle: 'b' });
    expect(verdict.ok).toBe(false);
    expect(verdict.code).toBe('CYCLE_DETECTED');
  });

  it('rejects incomplete connections', () => {
    expect(validateConnection(nodes, [], { source: 'n1', target: null, sourceHandle: 'out', targetHandle: null }).ok).toBe(false);
  });
});

describe('reaches', () => {
  it('follows edges forward only', () => {
    const edges: Edge[] = [
      { id: 'e1', source: 'n1', target: 'a1', sourceHandle: 'out', targetHandle: 'a' },
      { id: 'e2', source: 'a1', target: 'm1', sourceHandle: 'out', targetHandle: 'a' },
    ];
    expect(reaches(edges, 'n1', 'm1')).toBe(true);
    expect(reaches(edges, 'm1', 'n1')).toBe(false);
    expect(reaches(edges, 'm1', 'm1')).toBe(true); // trivial reachability of the start node
  });

  it('terminates on cyclic input', () => {
    const edges: Edge[] = [
      { id: 'e1', source: 'a', target: 'b', sourceHandle: 'out', targetHandle: 'a' },
      { id: 'e2', source: 'b', target: 'a', sourceHandle: 'out', targetHandle: 'a' },
    ];
    expect(reaches(edges, 'a', 'zzz')).toBe(false);
  });
});

describe('findInvalidEdges', () => {
  it('flags duplicates, self loops and unknown handles in a loaded graph', () => {
    const edges: Edge[] = [
      { id: 'ok', source: 'n1', target: 'a1', sourceHandle: 'out', targetHandle: 'a' },
      { id: 'dup', source: 'n2', target: 'a1', sourceHandle: 'out', targetHandle: 'a' },
      { id: 'self', source: 'm1', target: 'm1', sourceHandle: 'out', targetHandle: 'a' },
      { id: 'bad', source: 'n2', target: 'm1', sourceHandle: 'out', targetHandle: 'nope' },
    ];
    const problems = findInvalidEdges(nodes, edges);
    expect(problems.map((problem) => problem.edgeId).sort()).toEqual(['bad', 'dup', 'self']);
    expect(problems.find((problem) => problem.edgeId === 'dup')?.code).toBe('DUPLICATE_INPUT_EDGE');
    expect(problems.find((problem) => problem.edgeId === 'self')?.code).toBe('SELF_LOOP');
  });
});
