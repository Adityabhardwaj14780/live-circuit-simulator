# Node → netlist mapping

Authoritative implementation: [`services/sim/app/mapping.py`](../services/sim/app/mapping.py).
Display-only mirror for the UI: [`packages/graph-contract/src/netlistHints.ts`](../packages/graph-contract/src/netlistHints.ts).
One volt equals one unit of value: every node's output net carries that node's numeric result, and
the ground net `0` is the reference.

| Node type | SPICE device | Template | Notes |
|---|---|---|---|
| `number` | independent DC voltage source (`V`) | `{name} {net} {ground} DC {value}` | the literal is held on its own net |
| `add` | behavioural voltage source (`B`) | `{name} {net} {ground} V = V({in_a}) + V({in_b})` | `a`, `b` are required inputs |
| `multiply` | behavioural voltage source (`B`) | `{name} {net} {ground} V = V({in_a}) * V({in_b})` | `a`, `b` are required inputs |
| `divide` | behavioural voltage source (`B`) | `{name} {net} {ground} V = V({in_a}) / V({in_b})` | `a` = numerator, `b` = denominator; `b = 0` is rejected **before** ngspice runs |
| `formula` | behavioural voltage source (`B`) | `{name} {net} {ground} V = {expression}` | strictly parsed expression; identifiers become `V(net)` references |
| `display` | *(no device)* | `value = V({in_net})` | probe only: the display value is read from the operating-point results |

Naming: nets are generated (`n1`, `n2`, …) in evaluation order and elements are named `V1`, `B3`, …
Node ids are **never** interpolated into the deck — they stay in a map so that hostile ids or graph
names cannot inject deck instructions (see `sanitize_title` in `services/sim/app/netlist.py`).

## Example

Graph: `2 + 3` feeding a display, with `22 / 7` and a formula on the side.

```spice
* live-node-graph-engine | example
* revision 1 | nodes 5 | edges 4
* planned devices 4 | probes 1
V1 n1 0 DC 2
V2 n2 0 DC 3
B3 n3 0 V = V(n1) + V(n2)
B4 n4 0 V = (((V(n3) - V(n2)) / 2) + 0.5)
.op
.end
```

The deck always ends with `.op` then `.end`. It is executed with

```
ngspice -b -o <workdir>/ngspice.log -r <workdir>/operating_point.raw <workdir>/deck.cir
```

in a private temp directory (`.spiceinit` sets `filetype=ascii` and `numdgt=15`), with a hard timeout
(`SIM_TIMEOUT_S`, default 5 s) and a process-group `SIGKILL` on expiry.

Results are read from the ASCII raw file (`v(n1) …`, full double precision) and cross-checked against
the rounded `.op` node table (6 significant digits) that ngspice prints to the log; a disagreement
larger than 1e-5 relative is reported as a `NGSPICE_VALUE_MISMATCH` warning. If the raw file is
missing or binary, the engine falls back to the `.op` table.
