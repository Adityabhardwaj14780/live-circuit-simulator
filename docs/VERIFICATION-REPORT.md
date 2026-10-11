# Final verification report — Live Node Graph Engine

Date: 2026-10-11 · Scope: audit of all eight acceptance scenarios, graph validation / live sync / revision
handling, MongoDB persistence, the automated suites, netlist mappings and real ngspice execution.

## Verdict

Everything the assignment asks for is **implemented and verified by execution**, except **containerized
startup**: the Docker CLI is not installed in this sandbox, so `docker compose up --build` has never been run.
Three genuine defects were found during the audit and fixed (details below). No test was weakened, no result
mocked; every number in this report comes from a command that ran in this session.

## A. Acceptance scenarios — 8/8 PASS (102/102 live checks)

`node tests/e2e/acceptance-http.mjs` replays the fixtures from `services/sim/tests/acceptance_graphs.py` over
real HTTP against a running stack, and checks status, revision echo, every value, error codes, per-node
statuses, blocked/cyclic flags, netlist `contains`/`excludes` payload rules, rendered device lines, the deck
tail (`.op`/`.end`) and that ngspice execution really took measurable time.

| Scenario | Result | Checks | Key observed output |
|---|---|---|---|
| s1 `Number → Display` | **PASS** | 7/7 | display `7` |
| s2 `2 + 3 = 5` | **PASS** | 10/10 | `add1 = 5` |
| s3 `6 × 7 = 42` | **PASS** | 10/10 | `mul1 = 42` |
| s4 `22 / 7` | **PASS** | 10/10 | `3.142857142857143` (full precision) |
| s5 fan-out (1 output → 2 inputs) | **PASS** | 10/10 | `add1 = 10`, `mul1 = 25` |
| s6 missing input on `b` | **PASS** | 12/12 | `MISSING_INPUT` on handle `b`, display **blocked**, deck contains only `V1 n1 0 DC 7` |
| s7 cycle (`add→mul→add`) | **PASS** | 17/17 | `CYCLE_DETECTED` on `cyc_add`/`cyc_mul`, `d1` blocked, healthy `V1 n1 0 DC 1` still ran |
| s8 formula correctness + `10/0` + `__import__('os')` payload | **PASS** | 26/26 | `good_formula = 25`, `add_ok = 10`, `DIVIDE_BY_ZERO`, `FORMULA_DISALLOWED_TOKEN`; neither deck contains the payload |

Four independent front-door paths were exercised in the final sweep, all **102/102, 0 failures**:

| Path | Command | Result |
|---|---|---|
| Vite dev server, React StrictMode | `STACK_URL=http://localhost:5173 node tests/e2e/acceptance-http.mjs` | 102/102 |
| Production build served by `vite preview` | `STACK_URL=http://localhost:5174 …` | 102/102 |
| nginx 1.26 with the **real** `apps/web/nginx.conf` (static + `/api` proxy) | `STACK_URL=http://localhost:8080 …` | 102/102 |
| API from a **production-dependencies-only** bundle (81 packages, no devDeps) | `STACK_URL=http://localhost:4030 …` | 102/102 |

**Independent evidence.** `python3 tests/e2e/netlist-evidence.py` fetches the netlists the API actually
generated, runs each one directly under `ngspice -b` with a separate raw-output parser, and compares:

```
OK: every reported value matches an independent `ngspice -b` run of the same deck
```

So node values are produced by real ngspice execution, not by any shortcut in the application code.

## B. Behavioural requirements

| # | Requirement | Result | Evidence |
|---|---|---|---|
| 1 | React+TS+Vite+`@xyflow/react` / Express+TS / FastAPI+ngspice / MongoDB / Compose monorepo | **PASS** | tree + all suites; typechecks clean for 3 tsconfigs |
| 2 | Browser can reach **only** Express | **PASS** | only relative `/api`, `/health`; FastAPI and Mongo publish no ports in `docker-compose.yml`; verified through Vite proxy and nginx |
| 3 | Only Python executes ngspice | **PASS** | no ngspice reference in JS packages; executor is Python-only; `requirements`+Dockerfile install ngspice in the Python image |
| 4 | Palette: Number, Add, Multiply, Divide, Display + custom nodes with named handles | **PASS** | `ui-smoke.mjs` (real browser) + contract tests |
| 5 | One output → many inputs; one edge per input; invalid/self connections rejected | **PASS** | contract 16/16, API 25/25, UI refuses occupied inputs/self/loop-closing edges |
| 6 | Save/restore nodes, data, positions, edges | **PASS** | Mongo round-trip: revision 1→2, positions/data/edges identical field-by-field, restored graph re-executes (`12.5/4 = 3.125`) |
| 7 | Execute arbitrary graphs; nothing hard-coded | **PASS** | scenarios are data (`acceptance_graphs.py`); ad-hoc-graph genericity test builds an unseen graph and executes it |
| 8 | Exact node→netlist mappings | **PASS** | `app/mapping.py`; docs and UI hints asserted equal by `test_mapping_docs.py`; `docs/NETLIST-MAPPING.md` |
| 9 | Deck ends `.op` and `.end`; parsed values | **PASS** | asserted in every scenario's HTTP check and in `test_acceptance_graphs.py` |
| 10 | Formula node: `a`,`b`, literals, `+ - * /`, parens; strict parser; no `eval`/`exec`; rejected never reach ngspice | **PASS** | `test_formula.py` + 5 parametrised malicious/unsupported expressions; deck `excludes` the payload; no subprocess spawned for rejected graphs |
| 11 | No Run button; edits auto-execute | **PASS** | no run control in the UI; `ui-smoke.mjs` edits a value and asserts the canvas re-simulates without any click |
| 12 | Stale prevention: debounce + monotonic revisions + latest-revision check | **PASS** | web 19/19 (debounce, abort, stale, burst edits, recovery); API revision echo under 24 concurrent executes; ui-smoke burst-edit test |
| 13 | Node-specific errors + blocked downstream states; missing inputs and cycles detected pre-simulation | **PASS** | s6/s7 + `test_behaviour_matrix.py` (transitive blocking 3 hops; fully cyclic graph spawns **no** subprocess, `ngspiceMs == 0`) |
| 14 | Divide-by-zero, ngspice errors, timeouts, crashes, unavailable services; stays recoverable | **PASS** | pre-ngspice `DIVIDE_BY_ZERO`; timeout clears values, service healthy afterwards, subsequent run succeeds; simulated crash → structured error |
| 15 | Automated tests for the eight acceptance graphs incl. loop detection and unsafe-formula rejection | **PASS** | `test_acceptance_graphs.py` (+ HTTP replay of the same fixtures) |
| 16 | Separated modules: validation / netlist / formula / executor / parser | **PASS** | `services/sim/app/{graph,mapping,netlist,formula,executor,parser,engine}.py` |
| 17 | Structured errors with node IDs + stable codes | **PASS** | `{code, message, nodeIds, edgeIds}` asserted across suites; codes defined in the shared contract |
| 18 | Env vars; `.env.example` placeholders only | **PASS** | every setting has a default and an override; no secrets in repo |
| 19 | `docker compose up --build` from a clean clone | **UNVERIFIED** | Docker absent in this sandbox — see §D |
| 20 | README ≤ 2 pages incl. architecture, execution flow, cycle detection, Step 5 solutions, one known limitation, next steps, AI note | **PASS** | 1,244 words (≈1.9 pages at 650 words/page); all eight sections present |
| 21 | Design-first process; verifiable stages | **PASS** | `docs/DESIGN.md` committed before implementation |
| 22 | No mock results anywhere | **PASS** | values cross-checked against a direct `ngspice -b` run |

## C. Suites and exact outputs (final sweep, this session)

```
typechecks   packages/graph-contract OK · services/api OK · apps/web OK
contract     Tests  16 passed (16)
api          Tests  25 passed (25)
web          Tests  19 passed (19)
python       120 passed, 1 warning in 18.52s
acceptance   PASS s1 7/7 · s2 10/10 · s3 10/10 · s4 10/10 · s5 10/10 · s6 12/12 · s7 17/17 · s8 26/26
             total checks: 102, failures: 0      (repeated green on :5173, :5174, :8080, :4030)
evidence     OK: every reported value matches an independent `ngspice -b` run of the same deck
ui-smoke     ALL UI SMOKE CHECKS PASSED            (dev :5173 and nginx :8080)
compose      compose-spec schema: VALID · nginx: configuration file test is successful
```

Production build: `index-CRXjFwaS.js` 349.51 kB (gzip 112.74 kB) + `index-BiU2IeY-.css` 21.12 kB.
Simulation-image emulation (isolated copy using the image's COPY set, fresh venv, production deps):
`118 passed, 2 skipped` — the two skips are doc/TS-agreement tests, whose inputs that image intentionally
does not ship; they pass in the repository context (120 passed).

## D. Blockers and what could not run

1. **Docker is not installed** — no `docker`, `docker-compose` or `podman` binary and no
   `/var/run/docker.sock`. Therefore `docker compose up --build`, the `sim-tests` profile container, image
   builds, healthcheck wiring and inter-container DNS are **UNVERIFIED**. Compensating checks, all executed:
   Compose file validated against the official compose-spec JSON schema; the web image's nginx config run for
   real locally and the full acceptance replayed through it; the API run from a production-deps-only bundle;
   the simulation image's file layout, `CMD` and pytest command reproduced in a copy. These are emulations,
   not container runs, and are reported as such.
2. **The assignment's netlist-mapping table was never supplied.** The mapping is therefore centralized in
   `app/mapping.py` + `docs/NETLIST-MAPPING.md` (asserted identical by tests), so it can be corrected in one
   place if the official table differs.

## E. Defects found during this audit (all fixed, with regression tests)

| Defect | Impact | Fix |
|---|---|---|
| Blocked/error nodes returned empty `inputStates` | UI could not show *which* input was missing | `app/engine.py` computes input states for every node at assembly time |
| React StrictMode crash in `useLiveGraph` | Dev server rendered a crashed editor (`Cannot read properties of null`) while production builds hid it | engine created per effect run with a callbacks ref; regression test `useLiveGraph.test.tsx` |
| Display-template drift | `app/mapping.py` carried a comment absent from the docs/UI hints | templates aligned and locked by `test_mapping_docs.py` |

## F. Files changed in this audit round

- `services/sim/app/engine.py`, `services/sim/app/mapping.py`
- `services/sim/tests/`: `acceptance_graphs.py`, `test_acceptance_graphs.py`, `test_behaviour_matrix.py`,
  `test_mapping_docs.py`, `export_acceptance.py`, plus fixture-id renames in `test_api.py`,
  `test_graph_validation.py`, `test_netlist.py`
- `services/api/test/execute.test.ts`
- `apps/web/src/hooks/useLiveGraph.ts`, `apps/web/src/hooks/useLiveGraph.test.tsx`
- `tests/e2e/acceptance-http.mjs`, `tests/e2e/netlist-evidence.py`, `tests/e2e/ui-smoke.mjs`
- `README.md` (rewritten to verified reality), root `package.json` (new `test:ui`, `test:acceptance:http`,
  `test:evidence` scripts), `docs/screenshot-live.png` (fresh capture)
- This report; regenerated `__pycache__`/`.pytest_cache` artifacts removed

## G. Reproduce from scratch (no Docker)

```bash
npm install
npm run dev:api    # Express on :4000      (npm run dev:mongo for an in-memory MongoDB)
npm run dev:web    # Vite on :5173
(cd services/sim && python -m uvicorn app.main:app --port 8000)   # needs ngspice on PATH

npm run test:contract && npm run test:api && npm run test:web && npm run test:py
STACK_URL=http://localhost:5173 npm run test:acceptance:http
python3 tests/e2e/netlist-evidence.py
CHROMIUM_PATH=$(command -v chromium) UI_URL=http://localhost:5173 npm run test:ui
```

The live stack used for this report is still running: Mongo :27017, simulation :8000, API :4000, Vite :5173,
preview :5174, nginx :8080, prod-deps API emulation :4030. The nginx instance is a sandbox-only emulation and
can be stopped with `sudo /usr/sbin/nginx -s stop`.
