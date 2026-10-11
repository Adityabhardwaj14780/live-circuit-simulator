# Design — Live Node Graph Engine

Stage 0 deliverable: repository tree, graph schema, API contracts, netlist execution flow, implementation sequence.

## 1. Repository tree

```
live-node-graph-engine/
├── docker-compose.yml            # 4 services: mongo, sim (FastAPI+ngspice), api (Express), web (Vite)
├── .env.example                  # placeholders only, no secrets
├── package.json                  # npm workspaces root + convenience scripts
├── README.md                     # ≤ 2 pages, architecture / flow / Step 5 solutions
├── docs/
│   ├── DESIGN.md                 # this file
│   └── NETLIST-MAPPING.md        # the node → netlist mapping table (one place to swap)
├── packages/graph-contract/      # single source of truth for node/handle schema
│   ├── graph.schema.json         # JSON Schema (nodes, edges, handles, error codes)
│   ├── src/index.ts              # TS types + handle registry + structural validator
│   └── test/contract.test.ts
├── apps/web/                     # React + TS + Vite + @xyflow/react
│   ├── Dockerfile                # build → static, served by nginx w/ /api proxy
│   └── src/
│       ├── api/client.ts         # ONLY talks to Express (never FastAPI/Mongo)
│       ├── engine/liveSync.ts    # debounce + revision gate + abort (no Run button)
│       ├── engine/validation.ts  # client-side connection rule enforcement
│       ├── graph/nodeDefs.tsx    # custom nodes, named handles
│       └── components/           # Palette, Toolbar, StatusPanel, ErrorBanner
├── services/api/                 # Node + Express + TS
│   ├── Dockerfile
│   └── src/
│       ├── app.ts / index.ts     # app factory (testable) + bootstrap
│       ├── config.ts             # env vars
│       ├── db/graphRepository.ts # MongoDB persistence
│       ├── modules/graphValidation.ts, simClient.ts, errors.ts, logger.ts
│       └── routes/health.ts, graph.ts, execute.ts
├── services/sim/                 # Python + FastAPI + ngspice (batch)
│   ├── Dockerfile                # installs ngspice via apt
│   ├── app/
│   │   ├── main.py               # routes + health
│   │   ├── schemas.py            # Pydantic contracts (mirror of JSON Schema)
│   │   ├── graph.py              # validation, connectivity, cycle detection (Kahn)
│   │   ├── formula.py            # strict tokenizer + recursive-descent parser + evaluator
│   │   ├── mapping.py            # node → netlist device mapping table
│   │   ├── netlist.py            # netlist generation (ends `.op` / `.end`)
│   │   ├── executor.py           # subprocess argv-only, timeout, kill, temp dir
│   │   ├── parser.py             # ngspice batch output → net voltages
│   │   └── errors.py             # stable error codes
│   └── tests/                    # pytest: 8 acceptance graphs + unit tests
└── tests/e2e/                    # cross-service smoke test (HTTP only)
```

Rationale: one workspace package (`graph-contract`) prevents the three languages from drifting; the Python side mirrors it and a
cross-language contract test asserts the mirror stays honest.

## 2. Graph schema

```ts
type NodeType = 'number' | 'add' | 'multiply' | 'divide' | 'formula' | 'display';

interface GraphNode { id: string; type: NodeType; position: {x:number;y:number}; data: Record<string, unknown>; }
interface GraphEdge { id: string; source: string; sourceHandle: string; target: string; targetHandle: string; }
interface Graph { id?: string; name?: string; nodes: GraphNode[]; edges: GraphEdge[]; version?: number; }
```

Handle registry (named handles, mirrored in `graph.schema.json`):

| type     | inputs                               | outputs |
|----------|--------------------------------------|---------|
| number   | —                                    | `out`   |
| add      | `a`, `b` (one edge each, required)   | `out`   |
| multiply | `a`, `b` (one edge each, required)   | `out`   |
| divide   | `a` (numerator), `b` (denominator)   | `out`   |
| formula  | `a`, `b` (each optional but referenced identifiers must be wired) | `out` |
| display  | `in`                                 | —       |

Node `data`: `number → { value: number }`, `formula → { expression: string }`, others `{}`.
Invariants: **fan-out allowed** (one output → many inputs) and **fan-in of exactly one edge per input handle**; self-connections
rejected; unknown handles rejected; duplicate edges to the same input rejected (server keeps the first, reports the rest).

## 3. API contracts

### Express (public surface — the only thing the browser may call)

| Method | Path | Body | Response |
|---|---|---|---|
| GET  | `/health` | — | `{status, uptime_s, mongo:{ok}, simulation:{ok}}` (+503 when a dependency is down) |
| GET  | `/api/graph` | — | `{graph, revision, updatedAt}` (creates default empty graph on first read) |
| PUT  | `/api/graph` | `{graph}` | `{graph, revision, updatedAt}` — server validates structurally, bumps `revision` |
| POST | `/api/graph/validate` | `{graph}` | `{ok, issues[], topology}` — dry run, no subprocess |
| POST | `/api/graph/execute` | `{graph, revision}` | `{revision, status, results[], netlist, timings, errors[]}` |
| GET  | `/api/meta` | — | node/handle registry so the UI and docs cannot drift |

Every error body: `{error: {code, message, nodeIds: string[], edgeIds: string[], details?}}`.

`POST /api/graph/execute` is a pure function of its body: the client is the source of truth while editing, so recomputation never
races a save. `revision` is echoed verbatim for the staleness check.

### FastAPI (internal only, `SIMULATION_SERVICE_URL`)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | reports `ngspice` binary presence/version, timeout config |
| POST | `/simulate` | `{graph, revision}` → per-node results, netlist, timings |
| GET | `/mapping` | netlist mapping table (docs/debugging) |

Per-node result: `{nodeId, status: ok|error|blocked, value|null, errorCode|null, message|null}`.

Stable error codes: `GRAPH_SCHEMA_INVALID, UNKNOWN_NODE_TYPE, UNKNOWN_HANDLE, SELF_LOOP, DUPLICATE_INPUT_EDGE,
MISSING_INPUT, CYCLE_DETECTED, NON_NUMERIC_VALUE, FORMULA_SYNTAX_ERROR, FORMULA_DISALLOWED_TOKEN,
DIVIDE_BY_ZERO, UPSTREAM_ERROR, NETLIST_ERROR, NGSPICE_ERROR, NGSPICE_TIMEOUT, NGSPICE_MISSING,
PARSE_ERROR, SIMULATION_SERVICE_UNAVAILABLE, DB_UNAVAILABLE, VALIDATION_FAILED`.

## 4. Execution flow

```
browser edit ──▶ liveSync (debounce 350 ms, rev++) ──▶ POST /api/graph/execute {graph, revision}
                                 │                          │
                                 │                          ├─ structural validation (mirror of contract)
                                 │                          └─▶ POST {SIM}/simulate ──▶ graph.py
                                 │                                                        ├─ handle/edge validation
                                 │                                                        ├─ Kahn topo sort ⇒ cycles + blocked set
ready/error state ◀── discard if revision ≠ latest ◀── results (node values, errors) ◀───├─ mapping.py + netlist.py
                    abort in-flight on new edit                                  ┌────────┴─ formula.py (strict parse)
                                                                                 │
                                                             executor.py (argv only, timeout, kill pg) ──▶ ngspice -b deck.cir
                                                                                 │
                                                             parser.py ◀──── stdout node-voltage table
```

Persistence runs on a slower debounce (800 ms) via `PUT /api/graph` and never blocks simulation.

Cycle detection: Kahn's algorithm on the edge set. Nodes left with non-zero in-degree after peeling are cycle members
(`CYCLE_DETECTED`); anything transitively downstream of a cycle member is `blocked` with `UPSTREAM_ERROR`. Cycles therefore never
reach the netlist — ngspice only ever sees a DAG.

Step-by-step simulate:
1. Validate schema → per-node structural errors.
2. Validate edges (handles, self-loops, duplicate inputs) → rejected edges reported, first-wins for duplicates.
3. Topological sort → cycle set + blocked set.
4. Evaluate `number` literals and `formula` expressions symbolically (strict parser); division by zero detected here, before any subprocess.
5. If any node is in a hard error state, downstream nodes are marked `blocked` and a netlist is still generated for the healthy
   subgraph so partially valid graphs keep producing useful numbers.
6. Generate netlist → write to a per-request temp dir → `ngspice -b -o log -r raw deck` with argv list, hard
   timeout, process-group kill on expiry.
7. Parse the full-precision ASCII raw file (the `.op` node table is capped at 6 significant digits and is kept
   as a fallback/cross-check); map nets back to node ids; merge with literal/symbolic results.
8. Return `results`, the generated netlist (for transparency) and timings.

Formula safety: tokenizer emits only NUMBER, IDENT(a|b), `+ - * / ( )`; anything else (letters, `^`, `**`, `;`, quotes, `$`,
backticks, brackets, commas, underscores) is `FORMULA_DISALLOWED_TOKEN`. Recursive-descent parser with precedence, unary sign,
depth/token/length caps. No `eval`/`exec` anywhere; rejected expressions never produce a netlist. Evaluation happens in Python
first, so a `0` denominator is caught before ngspice (which silently returns `1e+33` — verified).

## 5. Implementation sequence (verifiable stages)

| Stage | Work | Check |
|---|---|---|
| 0 | Design (this doc) | reviewed against requirements |
| 1 | `graph-contract` package | `vitest` contract tests |
| 2 | `services/sim` modules + netlist mapping | `pytest` unit tests |
| 3 | `services/sim` API + 8 acceptance graphs | `pytest` + live ngspice run |
| 4 | `services/api` (env, Mongo repo, sim client, routes) | `vitest` + supertest, real Mongo via `mongodb-memory-server` |
| 5 | `apps/web` (canvas, palette, custom nodes, live sync) | `vitest` + `vite build` + live end-to-end run |
| 6 | Docker Compose, `.env.example`, README | compose config parse + honest note that Docker was unavailable in the build sandbox |
| 7 | Full regression pass | all suites + e2e |

## 6. Known constraints / assumptions

* Node ids and graph names are user text: the netlist therefore uses generated net names (`n1..nN`) and element
  names (`V1`, `B3`) and maps them back to node ids, and the only place user text appears is a sanitized comment
  (newlines stripped) — a hostile graph name cannot inject a deck directive.
* Divide-by-zero is detected in Python; ngspice itself would return `1e+33` without an error (verified with ngspice 44.2).
* `SIM_TIMEOUT_S` (default 5 s) bounds every subprocess; on expiry the process group is killed.
