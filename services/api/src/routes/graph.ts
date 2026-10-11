import { Router } from 'express';
import { ERROR_CODES, LIMITS, NETLIST_HINTS, NODE_SPECS, EXAMPLE_GRAPH } from '@lng/graph-contract';
import type { GraphRepository } from '../db/graphRepository.js';
import type { Logger } from '../logger.js';
import { ApiError, ErrorCodes } from '../modules/errors.js';
import { assertExecutable, normalizeGraph, validateGraph } from '../modules/graphValidation.js';
import type { SimClient } from '../modules/simClient.js';

const GRAPH_ID_RE = /^[A-Za-z0-9_.:-]{1,64}$/;

export function graphRouter(deps: {
  log: Logger;
  repository: GraphRepository;
  sim: SimClient;
  defaultGraphId: string;
}): Router {
  const router = Router();

  const resolveGraphId = (raw: unknown): string => {
    if (raw === undefined || raw === null || raw === '') return deps.defaultGraphId;
    const value = String(raw);
    if (!GRAPH_ID_RE.test(value)) {
      throw new ApiError(400, ErrorCodes.badRequest, `invalid graph id: ${JSON.stringify(value)}`);
    }
    return value;
  };

  /** Node/handle registry + netlist mapping hints so UI and docs cannot drift from the contract. */
  router.get('/meta', (_req, res) => {
    res.json({ nodeSpecs: NODE_SPECS, limits: LIMITS, errorCodes: ERROR_CODES, netlistHints: NETLIST_HINTS, exampleGraph: EXAMPLE_GRAPH });
  });

  router.get('/graph', async (req, res, next) => {
    try {
      const graphId = resolveGraphId(req.query.graphId);
      const stored = await deps.repository.read(graphId);
      deps.log.debug('graph read', { graphId, revision: stored.revision });
      res.json({ graphId, ...stored });
    } catch (error) {
      next(error);
    }
  });

  router.put('/graph', async (req, res, next) => {
    try {
      const graphId = resolveGraphId(req.query.graphId ?? (req.body as { graphId?: string })?.graphId);
      const graph = normalizeGraph((req.body as { graph?: unknown })?.graph);
      const report = validateGraph(graph);
      const stored = await deps.repository.save(graphId, graph);
      deps.log.info('graph saved', {
        graphId,
        revision: stored.revision,
        nodes: graph.nodes.length,
        edges: graph.edges.length,
        issues: report.issues.length,
      });
      res.json({ graphId, ...stored, issues: report.issues });
    } catch (error) {
      next(error);
    }
  });

  /** Dry run: structural checks + topology without a subprocess. */
  router.post('/graph/validate', (req, res, next) => {
    try {
      const graph = normalizeGraph((req.body as { graph?: unknown })?.graph);
      res.json(validateGraph(graph));
    } catch (error) {
      next(error);
    }
  });

  /**
   * Execute a graph. The body carries the graph being edited (the client is the source of truth
   * while editing) and the client revision, which is echoed back so stale responses can be dropped.
   */
  router.post('/graph/execute', async (req, res, next) => {
    try {
      const body = req.body as { graph?: unknown; revision?: unknown };
      const graph = normalizeGraph(body.graph);
      const revision = Number.isFinite(Number(body.revision)) ? Number(body.revision) : 0;
      assertExecutable(graph);

      const started = Date.now();
      const result = await deps.sim.simulate(graph, revision);
      deps.log.info('graph executed', {
        revision,
        status: result.status,
        nodes: result.results.length,
        durationMs: Date.now() - started,
        ngspiceMs: result.timings?.ngspiceMs,
      });
      res.json(result);
    } catch (error) {
      next(error);
    }
  });

  return router;
}
