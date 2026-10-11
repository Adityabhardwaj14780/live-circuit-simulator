/**
 * Live acceptance check: replays all eight scenarios against the RUNNING stack over HTTP.
 *
 * Unlike the pytest suite (which calls the engine in-process), this exercises the real boundary the
 * browser uses:  web origin (nginx/vite) → Express → FastAPI → ngspice → response.
 *
 *   node tests/e2e/acceptance-http.mjs
 *   STACK_URL=http://localhost:5173 node tests/e2e/acceptance-http.mjs
 *
 * The scenarios come from services/sim/tests/acceptance_graphs.py via export_acceptance.py, so this
 * runner can never drift from the pytest fixtures. Exits non-zero on any failure.
 */
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '../..');
const stackUrl = (process.env.STACK_URL ?? 'http://localhost:5173').replace(/\/+$/, '');

const results = [];
let failures = 0;

function check(scenario, name, condition, detail = '') {
  const ok = Boolean(condition);
  if (!ok) failures += 1;
  results.push({ scenario, name, ok, detail });
  return ok;
}

function closeEnough(actual, expected, tolerance) {
  if (typeof actual !== 'number' || typeof expected !== 'number') return false;
  return Math.abs(actual - expected) <= tolerance;
}

// ---- load the scenarios from the python fixtures -------------------------------------------------
const scenariosJson = execFileSync('python3', [resolve(repoRoot, 'services/sim/tests/export_acceptance.py')], {
  encoding: 'utf8',
});
const { count, scenarios } = JSON.parse(scenariosJson);
console.log(`[acceptance] ${count} scenarios loaded from services/sim/tests/acceptance_graphs.py`);
console.log(`[acceptance] stack under test: ${stackUrl}\n`);

// ---- stack must be up ---------------------------------------------------------------------------
const health = await fetch(`${stackUrl}/health`).then((response) => response.json());
console.log('[acceptance] /health →', JSON.stringify({
  status: health.status,
  mongo: health.mongo?.ok,
  ngspice: health.simulation?.ngspice?.available,
  version: health.simulation?.ngspice?.version,
}));
if (health.simulation?.ngspice?.available !== true) {
  console.error('[acceptance] ngspice is not available in the stack; aborting');
  process.exit(2);
}

// ---- replay each scenario -----------------------------------------------------------------------
for (const scenario of scenarios) {
  const revision = Math.floor(Math.random() * 1_000_000) + 1;
  const response = await fetch(`${stackUrl}/api/graph/execute`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ graph: scenario.graph, revision }),
  });
  const body = await response.json();
  const byId = Object.fromEntries((body.results ?? []).map((entry) => [entry.nodeId, entry]));
  const netlist = body.netlist ?? '';
  const deviceLines = netlist.split('\n').filter((line) => line[0] === 'V' || line[0] === 'B');

  check(scenario.id, 'HTTP 200', response.status === 200, `status ${response.status}`);
  check(scenario.id, `overall status = ${scenario.expectStatus}`, body.status === scenario.expectStatus, `got ${body.status}`);
  check(scenario.id, `revision ${revision} echoed`, body.revision === revision, `got ${body.revision}`);

  for (const [nodeId, expected] of Object.entries(scenario.expectValues ?? {})) {
    const entry = byId[nodeId] ?? {};
    check(
      scenario.id,
      `${nodeId} = ${expected}`,
      entry.status === 'ok' && closeEnough(entry.value, expected, scenario.expectValueTolerance ?? 1e-9),
      `got status=${entry.status} value=${entry.value}`,
    );
  }
  for (const [nodeId, code] of Object.entries(scenario.expectErrorCodes ?? {})) {
    const entry = byId[nodeId] ?? {};
    check(scenario.id, `${nodeId} → ${code}`, entry.errorCode === code, `got ${entry.errorCode}`);
  }
  for (const [nodeId, status] of Object.entries(scenario.expectStatuses ?? {})) {
    check(scenario.id, `${nodeId} status = ${status}`, (byId[nodeId] ?? {}).status === status, `got ${(byId[nodeId] ?? {}).status}`);
  }
  for (const nodeId of scenario.expectBlocked ?? []) {
    check(scenario.id, `${nodeId} is blocked`, (byId[nodeId] ?? {}).status === 'blocked');
  }
  for (const nodeId of scenario.expectCyclic ?? []) {
    check(scenario.id, `${nodeId} is in a cycle`, (body.topology?.cyclic ?? []).includes(nodeId));
  }
  for (const needle of scenario.expectNetlistContains ?? []) {
    check(scenario.id, `netlist contains ${JSON.stringify(needle)}`, netlist.includes(needle));
  }
  for (const needle of scenario.expectNetlistExcludes ?? []) {
    check(scenario.id, `netlist excludes ${JSON.stringify(needle)}`, !netlist.includes(needle));
  }
  if (scenario.expectDeviceLines) {
    check(
      scenario.id,
      `device lines = ${JSON.stringify(scenario.expectDeviceLines)}`,
      JSON.stringify(deviceLines) === JSON.stringify(scenario.expectDeviceLines),
      JSON.stringify(deviceLines),
    );
  }
  if (netlist.trim()) {
    const tail = netlist.split('\n').filter((line) => line.trim()).slice(-2);
    check(scenario.id, 'deck ends with .op/.end', tail[0] === '.op' && tail[1] === '.end', JSON.stringify(tail));
  }

  // values must come from a real ngspice run
  if (scenario.expectStatus !== 'error') {
    check(scenario.id, 'ngspice actually executed', (body.timings?.ngspiceMs ?? 0) > 0, `ngspiceMs=${body.timings?.ngspiceMs}`);
  }

  console.log(`[acceptance] ${scenario.id.padEnd(22)} ${body.status.padEnd(8)} ngspice ${String(body.timings?.ngspiceMs ?? 0).padStart(6)} ms  values=${JSON.stringify(
    Object.fromEntries(Object.entries(byId).map(([id, entry]) => [id, entry.value])),
  )}`);
}

// ---- summary ------------------------------------------------------------------------------------
const byScenario = new Map();
for (const result of results) {
  byScenario.set(result.scenario, [...(byScenario.get(result.scenario) ?? []), result]);
}
console.log('\n================ acceptance summary ================');
for (const [scenario, checks] of byScenario) {
  const failed = checks.filter((entry) => !entry.ok);
  const verdict = failed.length === 0 ? 'PASS' : 'FAIL';
  console.log(`${verdict}  ${scenario}  (${checks.length - failed.length}/${checks.length} checks)`);
  for (const failure of failed) console.log(`        ✗ ${failure.name} — ${failure.detail}`);
}
console.log(`\ntotal checks: ${results.length}, failures: ${failures}`);
process.exit(failures === 0 ? 0 : 1);
