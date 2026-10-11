import { Router } from 'express';
import type { ApiConfig } from '../config.js';
import type { GraphRepository } from '../db/graphRepository.js';
import type { Logger } from '../logger.js';
import type { SimClient } from '../modules/simClient.js';

export function healthRouter(deps: { config: ApiConfig; log: Logger; repository: GraphRepository; sim: SimClient }): Router {
  const router = Router();

  router.get('/health', async (_req, res) => {
    const started = Date.now();
    const [mongoOk, simHealth] = await Promise.all([deps.repository.ping(), deps.sim.health()]);
    const simulationOk = simHealth.status !== 'unreachable';
    const status = !mongoOk ? 'error' : simulationOk ? 'ok' : 'degraded';

    res.status(mongoOk ? 200 : 503).json({
      status,
      service: 'api',
      uptimeSeconds: Number(process.uptime().toFixed(3)),
      mongo: { ok: mongoOk, db: deps.config.mongoDbName },
      simulation: simHealth,
      nodeEnv: deps.config.nodeEnv,
      checkDurationMs: Date.now() - started,
    });
  });

  return router;
}
