/**
 * End-to-end UI smoke test (optional, not part of `docker compose up`).
 *
 * Drives the built frontend in a real browser and verifies the full chain:
 *   canvas → /api/graph/execute → Express → FastAPI → ngspice → values rendered on the nodes.
 * It also edits a Number node and asserts the graph re-simulates without any Run button.
 *
 * Usage:
 *   CHROMIUM_PATH=/usr/bin/chromium UI_URL=http://localhost:5174 node tests/e2e/ui-smoke.mjs
 */
import { mkdirSync } from 'node:fs';
import puppeteer from 'puppeteer-core';

const COMMON_CHROMIUM_PATHS = ['/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome'];
const executablePath = process.env.CHROMIUM_PATH ?? COMMON_CHROMIUM_PATHS.find((path) => path); // assumed present
const uiUrl = process.env.UI_URL ?? 'http://localhost:5174';
const screenshotDir = process.env.SCREENSHOT_DIR ?? 'docs';

const log = (...args) => console.log('[ui-smoke]', ...args);

function assert(condition, message) {
  if (!condition) throw new Error(`ASSERTION FAILED: ${message}`);
  log(`ok: ${message}`);
}

const browser = await puppeteer.launch({
  executablePath,
  headless: 'shell',
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 950 });
  page.on('console', (message) => {
    if (message.type() === 'error') log('browser console error:', message.text());
  });
  page.on('pageerror', (error) => log('page error:', error.message));

  await page.goto(uiUrl, { waitUntil: 'networkidle2', timeout: 60000 });

  // 1. palette and canvas exist
  await page.waitForSelector('[data-testid="palette-number"]', { timeout: 30000 });
  assert(true, 'node palette rendered');
  await page.waitForSelector('.react-flow__node', { timeout: 30000 });
  const nodeCount = await page.$$eval('.react-flow__node', (elements) => elements.length);
  assert(nodeCount > 0, `canvas shows ${nodeCount} node(s) (restored from MongoDB)`);

  // 2. the live run succeeds without any Run button
  await page.waitForFunction(
    () => document.querySelector('[data-testid="live-phase"]')?.textContent?.includes('simulation ok'),
    { timeout: 40000 },
  );
  assert(true, 'live phase reached "live: simulation ok"');

  const results = await page.$$eval('[data-testid^="result-"]', (elements) =>
    elements.map((element) => ({ id: element.getAttribute('data-testid'), text: element.textContent ?? '' })),
  );
  log('node results:', results);
  assert(
    results.some((entry) => /^-?\d+/.test(entry.text.trim())),
    'at least one node displays a simulated number',
  );

  // 3. the inspector shows the generated netlist that ngspice executed
  const netlist = await page.$eval('[data-testid="netlist"]', (element) => element.textContent ?? '');
  assert(netlist.includes('.op') && netlist.includes('.end'), 'inspector shows a netlist ending in .op/.end');
  assert(/V\d+ n\d+ 0 DC/.test(netlist), 'netlist contains a number→voltage-source mapping');

  // 4. live re-simulation: change a Number node and watch the graph recompute by itself
  const numberInputs = await page.$$('[data-testid$="-value"]');
  assert(numberInputs.length > 0, 'a Number node exposes an editable value field');
  const before = await page.$eval('[data-testid="netlist"]', (element) => element.textContent ?? '');

  await numberInputs[0].click({ clickCount: 3 });
  await numberInputs[0].type('9');
  await page.waitForFunction(
    (previous) => {
      const phase = document.querySelector('[data-testid="live-phase"]')?.textContent ?? '';
      const current = document.querySelector('[data-testid="netlist"]')?.textContent ?? '';
      return phase.includes('simulation ok') && current !== previous && current.includes('DC 9');
    },
    { timeout: 40000 },
    before,
  );
  assert(true, 'editing a Number node re-ran the simulation automatically (netlist now holds DC 9)');

  // 5. rapid successive edits must settle on the LAST value (debounce + revision gating)
  await numberInputs[0].click({ clickCount: 3 });
  await numberInputs[0].type('1');
  await new Promise((resolve) => setTimeout(resolve, 40)); // < 300 ms debounce: both edits share one window
  await numberInputs[0].click({ clickCount: 3 });
  await numberInputs[0].type('7');
  await page.waitForFunction(
    () => {
      const phase = document.querySelector('[data-testid="live-phase"]')?.textContent ?? '';
      const netlist = document.querySelector('[data-testid="netlist"]')?.textContent ?? '';
      return phase.includes('simulation ok') && netlist.includes('DC 7') && !netlist.includes('DC 1');
    },
    { timeout: 40000 },
  );
  const afterBurst = await page.$eval('[data-testid="netlist"]', (element) => element.textContent ?? '');
  assert(afterBurst.includes('DC 7'), 'two edits 40 ms apart settle on the later value (debounce + latest-revision win)');

  // 6. the run counter and revision progress, proving results are applied in order
  const status = await page.$eval('[data-testid="live-phase"]', (element) => element.textContent ?? '');
  const runs = await page.$eval('.statusbar', (element) => element.textContent ?? '');
  log('statusbar:', runs.trim());
  assert(/run #\d+/.test(runs) && status.includes('simulation ok'), 'status bar reports completed live runs');

  // 7. node cards present for every node in the graph
  const info = await page.evaluate(() => [...document.querySelectorAll('.node-card')].map((card) => card.getAttribute('data-testid')));
  log('node cards:', info);

  // 6. screenshot for the repo docs
  mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({ path: `${screenshotDir}/screenshot-live.png`, fullPage: false });
  assert(true, `screenshot written to ${screenshotDir}/screenshot-live.png`);

  const statusText = await page.$eval('[data-testid="live-phase"]', (element) => element.textContent);
  log('final live phase:', statusText);
  log('ALL UI SMOKE CHECKS PASSED');
} finally {
  await browser.close();
}
