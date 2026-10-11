/**
 * Node definitions + the custom node renderer.
 *
 * Handles are named exactly as the contract declares them (add/multiply/divide/formula: `a`, `b`;
 * number: `out`; display: `in`), so the ids stored in React Flow are the ids the backend validates.
 * Editing writes straight back into node data through React Flow's own `updateNodeData`, which keeps
 * the canvas the single source of truth for live re-simulation.
 */
import { Handle, Position, useReactFlow, type Node, type NodeProps } from '@xyflow/react';
import { getNodeSpec, type NodeType } from '@lng/graph-contract';
import type { LiveNodeResult } from '../engine/liveSync';

export interface NodePayload extends Record<string, unknown> {
  value?: number;
  expression?: string;
  result?: LiveNodeResult | null;
  stale?: boolean;
}

export type GraphEditorNode = Node<NodePayload>;

export const NODE_TYPES_ORDER: NodeType[] = ['number', 'add', 'multiply', 'divide', 'formula', 'display'];

export const NODE_ACCENT: Record<NodeType, string> = {
  number: '#2563eb',
  add: '#0d9488',
  multiply: '#7c3aed',
  divide: '#c2410c',
  formula: '#db2777',
  display: '#475569',
};

export function defaultDataFor(type: NodeType): NodePayload {
  if (type === 'number') return { value: 1 };
  if (type === 'formula') return { expression: 'a + b' };
  return {};
}

export const HANDLE_LABELS: Record<string, string> = { a: 'a', b: 'b', in: 'in', out: 'out' };

export function formatValue(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  if (Number.isInteger(value)) return String(value);
  return Number(value.toPrecision(10)).toString();
}

export function CustomNode({ id, type, data, selected }: NodeProps) {
  const { updateNodeData } = useReactFlow();
  const nodeType = (type ?? 'display') as NodeType;
  const spec = getNodeSpec(nodeType);
  const result = data?.result as LiveNodeResult | null | undefined;
  const stale = Boolean(data?.stale);
  const stateClass = result ? `state-${result.status}` : stale ? 'state-stale' : 'state-idle';
  const accent = NODE_ACCENT[nodeType] ?? '#475569';

  const inputState = (handle: string): string => result?.inputStates?.[handle] ?? '';
  const issueFor = (handle: string): { code: string; message: string } | null => {
    const match = (result?.issues ?? []).find((issue) => issue.handle === handle);
    return match ? { code: match.code, message: match.message } : null;
  };

  return (
    <div className={`node-card ${stateClass} ${selected ? 'selected' : ''}`} style={{ borderTopColor: accent }} data-testid={`node-${id}`}>
      <header className="node-card__header" style={{ background: accent }}>
        <span className="node-card__type">{spec?.label ?? nodeType}</span>
        <span className="node-card__id">{id}</span>
      </header>

      <div className="node-card__body">
        {(spec?.inputs ?? []).map((handle, index) => {
          const issue = issueFor(handle);
          const state = inputState(handle);
          return (
            <div className="node-card__row" key={handle} style={{ marginTop: index === 0 ? 0 : 6 }}>
              <Handle
                type="target"
                position={Position.Left}
                id={handle}
                className={`handle handle--${handle} ${state === 'missing' ? 'handle--missing' : ''} ${issue ? 'handle--issue' : ''}`}
                data-testid={`handle-${id}-in-${handle}`}
              />
              <span className="node-card__label">
                {HANDLE_LABELS[handle] ?? handle}
                {nodeType === 'divide' && handle === 'b' ? ' (divisor)' : ''}
              </span>
            </div>
          );
        })}

        {nodeType === 'number' && (
          <label className="node-card__field">
            <span>value</span>
            <input
              type="number"
              value={String(data?.value ?? 0)}
              onChange={(event) => updateNodeData(id, { value: Number(event.target.value) })}
              data-testid={`input-${id}-value`}
            />
          </label>
        )}

        {nodeType === 'formula' && (
          <label className="node-card__field">
            <span>expr</span>
            <input
              type="text"
              spellCheck={false}
              value={String(data?.expression ?? '')}
              onChange={(event) => updateNodeData(id, { expression: event.target.value })}
              data-testid={`input-${id}-expression`}
            />
          </label>
        )}

        <div className="node-card__result" data-testid={`result-${id}`}>
          {result?.status === 'ok' && <strong>{formatValue(result.value)}</strong>}
          {result?.status === 'error' && <span className="result-error">{result.errorCode}</span>}
          {result?.status === 'blocked' && <span className="result-blocked">blocked</span>}
          {!result && <span className="result-idle">{stale ? 'stale' : 'idle'}</span>}
        </div>

        {result?.message && <div className="node-card__message">{result.message}</div>}

        {(spec?.outputs?.length ?? 0) > 0 && (
          <div className="node-card__row node-card__row--out">
            <span className="node-card__label">{HANDLE_LABELS.out}</span>
            <Handle type="source" position={Position.Right} id="out" className="handle" data-testid={`handle-${id}-out`} />
          </div>
        )}
      </div>
    </div>
  );
}
