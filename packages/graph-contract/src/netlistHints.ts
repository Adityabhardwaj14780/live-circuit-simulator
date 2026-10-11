/**
 * Display-only hints mirroring docs/NETLIST-MAPPING.md and services/sim/app/mapping.py — the exact
 * templates the Python service renders. The UI shows these so users can see what a node compiles to;
 * they are never used for execution, and the templates are asserted against mapping.py in tests.
 */
import type { NodeType } from './types';

export interface MappingHint {
  nodeType: NodeType;
  /** Template with {name}, {net}, {ground}, {in_*} placeholders — same shape as mapping.py. */
  template: string;
  description: string;
}

export const NETLIST_HINTS: Record<NodeType, MappingHint> = {
  number: {
    nodeType: 'number',
    template: '{name} {net} {ground} DC {value}',
    description: 'Independent DC voltage source holds the literal (1 V == 1 unit of value).',
  },
  add: {
    nodeType: 'add',
    template: '{name} {net} {ground} V = V({in_a}) + V({in_b})',
    description: 'Behavioural voltage source summing its input nets.',
  },
  multiply: {
    nodeType: 'multiply',
    template: '{name} {net} {ground} V = V({in_a}) * V({in_b})',
    description: 'Behavioural voltage source multiplying its input nets.',
  },
  divide: {
    nodeType: 'divide',
    template: '{name} {net} {ground} V = V({in_a}) / V({in_b})',
    description: 'a = numerator, b = denominator; a zero denominator is rejected before ngspice runs.',
  },
  formula: {
    nodeType: 'formula',
    template: '{name} {net} {ground} V = {expression}',
    description: 'Strictly parsed expression; identifiers become net voltage references V(n..).',
  },
  display: {
    nodeType: 'display',
    template: 'value = V({in_net})',
    description: 'Display adds no device: it reports its upstream net voltage from the .op results.',
  },
};
