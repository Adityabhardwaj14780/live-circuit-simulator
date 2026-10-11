import { NODE_SPECS, type NodeType } from '@lng/graph-contract';
import { NODE_ACCENT, NODE_TYPES_ORDER } from '../graph/nodeDefs';

const BLURBS: Record<NodeType, string> = {
  number: 'literal constant',
  add: 'a + b',
  multiply: 'a * b',
  divide: 'a / b (b ≠ 0)',
  formula: 'strict expression',
  display: 'probe upstream net',
};

export function Palette({ onAdd }: { onAdd: (type: NodeType) => void }) {
  return (
    <aside className="palette">
      <h2>Node palette</h2>
      <p className="palette__hint">Click to add, or drag onto the canvas.</p>
      {NODE_TYPES_ORDER.map((type) => (
        <button
          key={type}
          type="button"
          className="palette__item"
          style={{ borderLeftColor: NODE_ACCENT[type] }}
          draggable
          onDragStart={(event) => {
            event.dataTransfer.setData('application/lng-node-type', type);
            event.dataTransfer.effectAllowed = 'move';
          }}
          onClick={() => onAdd(type)}
          data-testid={`palette-${type}`}
        >
          <span className="palette__title">{NODE_SPECS[type].label}</span>
          <span className="palette__blurb">{BLURBS[type]}</span>
          <span className="palette__ports">
            in: {NODE_SPECS[type].inputs.length ? NODE_SPECS[type].inputs.join(', ') : '—'} · out:{' '}
            {NODE_SPECS[type].outputs.length ? NODE_SPECS[type].outputs.join(', ') : '—'}
          </span>
        </button>
      ))}
    </aside>
  );
}
