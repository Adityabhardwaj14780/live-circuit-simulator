# Live Node Graph Engine

Browser-based visual node-graph editor whose graphs are executed **for real**: React Flow canvas → Express API →
FastAPI → `ngspice -b` → numbers back on the nodes. Every edit re-simulates; there is no Run button.

**Stack** React · TypeScript · Vite · `@xyflow/react` · Node + Express · Python + FastAPI · ngspice (batch) ·
MongoDB · Docker Compose.

![editor](docs/screenshot-live.png)

## Architecture

```
browser (nginx :5173)         api (Express :4000)          simulation (FastAPI :8000)      mongo (:27017)
┌──────────────────┐   /api  ┌────────────────┐    HTTP    ┌────────────────────────┐   ┌────────────┐
│ React Flow canvas│ ───────▶│ routes + Mongo │ ─────────▶ │ validate → netlist →   │   │ graphs     │
│ live-sync engine │◀─────── │ repository     │◀─────────  │ ngspice -b → parse     │   │ document   │
└──────────────────┘ results └────────────────┘  results   └────────────────────────┘   └────────────┘
     only relative /api + /health requests            ngspice executes ONLY in the simulation image
```

* The browser talks **only to Express**; FastAPI and MongoDB publish no ports, so ngspice and the database are
  unreachable from the client.
* `packages/graph-contract/graph.schema.json` is the one contract (node types, handles, data fields, limits,
  error codes); TS derives from it and `test_contract_mirror.py` fails if Python drifts.
* `services/sim/app/`: `graph.py` validation/connectivity/Tarjan cycles · `formula.py` strict
  tokenizer+parser · `mapping.py` node→device table · `netlist.py` decks · `executor.py` subprocess ·
  `parser.py` output · `engine.py` orchestration.

Directories: `packages/graph-contract` · `apps/web` · `services/api` · `services/sim` · `tests/e2e`.

## Quick start

```bash
cp .env.example .env          # placeholders only
docker compose up --build     # editor http://localhost:5173 · API http://localhost:4000
docker compose --profile test run --rm sim-tests   # acceptance suite inside Docker (real ngspice)
```

Without Docker: `npm install`, then `npm run dev --workspace @lng/api`, `npm run dev --workspace @lng/web`,
`python -m uvicorn app.main:app --port 8000` in `services/sim`, and `npm run dev:mongo --workspace @lng/api`.

**API**: `GET /health` · `GET /api/meta` · `GET|PUT /api/graph` · `POST /api/graph/validate` ·
`POST /api/graph/execute {graph, revision}`. Errors are always `{error:{code, message, nodeIds, edgeIds}}`
with stable codes (`MISSING_INPUT`, `DIVIDE_BY_ZERO`, `CYCLE_DETECTED`, `FORMULA_DISALLOWED_TOKEN`,
`NGSPICE_TIMEOUT`, `SIMULATION_SERVICE_UNAVAILABLE`, …).

## Execution flow

1. **Edit** → values go stale, execution debounced 300 ms, local revision bumped.
2. **Execute** → the next edit aborts the in-flight request; a response is applied only if its revision is
   still the latest.
3. **Validate** → schema, handle names, one edge per input (first wins, later ones reported), required inputs,
   cycles. The canvas also refuses self-connections, occupied inputs and loop-closing edges.
4. **Checks** → literals, strict formula parse. Division by zero is caught here, because ngspice returns
   `1e+33` without complaining (verified with ngspice 44.2).
5. **Netlist** → `number` → `V{name} {net} 0 DC {value}`; `add`/`multiply`/`divide`/`formula` →
   `B{name} {net} 0 V = …`; `display` probes its upstream net and adds no device; the deck ends `.op`/`.end`.
   Nets and devices use generated names, so user text can never inject a directive
   (`docs/NETLIST-MAPPING.md`, asserted by `test_mapping_docs.py`).
6. **Run** → `ngspice -b -o log -r raw deck` in a temp dir: argv-only (no shell), hard timeout
   (`SIM_TIMEOUT_S`, default 5 s), process-group `SIGKILL` on expiry.
7. **Parse** → full-precision ASCII raw file, with the 6-digit `.op` table as fallback and cross-check.
8. **Assemble** → per-node values, `blocked` propagation, per-input states, structured errors, timings; the
   inspector shows the netlist that actually ran.

**Cycle detection:** iterative Tarjan SCCs identify cycle members; descendants of a cycle are `blocked` with
`UPSTREAM_ERROR` instead of being misreported as cyclic (a Kahn peel alone cannot tell those apart — a
contract test caught exactly that). Cycles are refused before netlist generation, so ngspice only ever runs a
DAG; a fully cyclic graph spawns no subprocess at all.

## Step 5 solutions (live sync, no Run button)

1. **Fast edits:** Edits debounce 300 ms for simulation and 900 ms for MongoDB persistence. Each run tags an incrementing local revision and cancels in-flight requests using `AbortController`. When responses arrive (even out of order), `LiveSyncEngine` applies results only if the response revision matches the latest requested revision; older responses are discarded.
2. **Errors on the right node:** Pre-simulation static folding catches unwired required inputs and division by zero (`b == 0` or within 1e-12) before calling ngspice, attributing errors directly to the offending node and handle. Iterative Tarjan SCC identifies all cycle members; descendants of broken nodes or cycles are marked `blocked` (`UPSTREAM_ERROR`) rather than showing stale values.
3. **ngspice misbehaves:** ngspice runs in batch mode (`-b`) in a private temporary directory under a strict wall-clock timeout (`SIM_TIMEOUT_S`, default 5 s). On timeout or crash, the process group is `SIGKILL`ed, temporary artifacts are cleaned up in `finally`, and affected nodes report structured errors without crashing the backend. The UI surfaces connection state and provides a "Retry simulation" control.
4. **Untrusted formulas:** Formula expressions are parsed with a strict hand-written recursive-descent tokenizer and AST parser in Python (zero `eval`/`exec`). Only numbers, identifiers `a`/`b`, `+ - * /`, and parentheses are permitted; any disallowed token (newlines, dots, shell commands like `a\n.control\nshell ls`) aborts validation immediately (`FORMULA_DISALLOWED_TOKEN`). Rejected expressions never reach netlist generation or ngspice.

## Next steps & Live session guide

**What I'd do next:** Multiple named graphs + revision rollback · streaming per-node results (SSE/WebSocket) · extra node types (`subtract`, `min`, `max`, comparisons) · transient/sweep analyses (`.dc`/`.ac`/`.tran`).

