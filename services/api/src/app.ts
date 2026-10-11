/** Express application factory — dependencies are injected so routes are testable without Mongo or ngspice. */
import cors from 'cors';
import express, { type NextFunction, type Request, type Response } from 'express';
import type { ApiConfig } from './config.js';
import type { GraphRepository } from './db/graphRepository.js';
import type { Logger } from './logger.js';
import { ApiError, ErrorCodes, isApiError } from './modules/errors.js';
import type { SimClient } from './modules/simClient.js';
import { graphRouter } from './routes/graph.js';
import { healthRouter } from './routes/health.js';

export interface AppDeps {
  config: ApiConfig;
  log: Logger;
  repository: GraphRepository;
  sim: SimClient;
}

export function createApp(deps: AppDeps): express.Express {
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({ limit: '2mb' }));
  app.use(
    cors({
      origin: deps.config.corsOrigins === '*' ? true : deps.config.corsOrigins,
      methods: ['GET', 'POST', 'PUT', 'OPTIONS'],
      allowedHeaders: ['content-type'],
    }),
  );

  // request logging
  app.use((req, res, next) => {
    const started = Date.now();
    res.on('finish', () => {
      deps.log.info('http', { method: req.method, path: req.originalUrl, status: res.statusCode, ms: Date.now() - started });
    });
    next();
  });

  app.get('/', (_req, res) => {
    res.json({ service: 'api', endpoints: ['/health', '/api/meta', '/api/graph', '/api/graph/validate', '/api/graph/execute'] });
  });

  app.use(healthRouter(deps));
  app.use('/api', graphRouter({ log: deps.log, repository: deps.repository, sim: deps.sim, defaultGraphId: deps.config.defaultGraphId }));

  app.use((req, res, next) => {
    next(new ApiError(404, ErrorCodes.notFound, `no route for ${req.method} ${req.originalUrl}`));
  });

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  app.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
    if (error instanceof SyntaxError && 'body' in error) {
      res.status(400).json({ error: { code: ErrorCodes.badRequest, message: 'request body is not valid JSON', nodeIds: [], edgeIds: [] } });
      return;
    }
    if (isApiError(error)) {
      res.status(error.status).json({ error: error.toBody() });
      return;
    }
    deps.log.error('unhandled api error', { error: error instanceof Error ? error.message : String(error) });
    res.status(500).json({
      error: { code: ErrorCodes.simulationError, message: 'unexpected server error', nodeIds: [], edgeIds: [] },
    });
  });

  return app;
}
