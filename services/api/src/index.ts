/** Bootstrap: connect Mongo (with retries), build the app, listen, shut down gracefully. */
import 'dotenv/config';
import { createApp } from './app.js';
import { config } from './config.js';
import { MongoGraphRepository, MemoryGraphRepository, type GraphRepository } from './db/graphRepository.js';
import { connectMongo } from './db/mongo.js';
import { createLogger, type Logger } from './logger.js';
import { createSimClient } from './modules/simClient.js';

const log = createLogger(config.logLevel, { service: 'api' });

async function main(): Promise<void> {
  const sim = createSimClient(config, log);

  let repository: GraphRepository;
  try {
    const { client, dbName } = await connectMongo(config, log);
    repository = new MongoGraphRepository(client, client.db(dbName));
  } catch (error) {
    // Without Mongo there is nowhere durable to store graphs; refuse to start rather than pretend.
    log.error('cannot start without MongoDB', { error: (error as Error).message, mongoUrl: config.mongoUrl });
    process.exit(1);
  }

  if (process.env.ALLOW_MEMORY_FALLBACK === 'true') {
    log.warn('ALLOW_MEMORY_FALLBACK is set; falling back to an in-memory repository is available on ping failure');
    repository = new FallbackRepository(repository, log);
  }

  const app = createApp({ config, log, repository, sim });
  const server = app.listen(config.port, '0.0.0.0', () => {
    log.info('api listening', {
      port: config.port,
      simulationServiceUrl: config.simulationServiceUrl,
      mongoDb: config.mongoDbName,
      corsOrigins: config.corsOrigins,
    });
  });

  const shutdown = async (signal: string): Promise<void> => {
    log.info('shutting down', { signal });
    server.close();
    await repository.close().catch(() => undefined);
    process.exit(0);
  };
  process.on('SIGTERM', () => void shutdown('SIGTERM'));
  process.on('SIGINT', () => void shutdown('SIGINT'));
}

/** Swaps to an in-memory store when the primary repository stops responding (opt-in, never silent). */
class FallbackRepository implements GraphRepository {
  private fallback = new MemoryGraphRepository();

  constructor(private readonly primary: GraphRepository, private readonly log: Logger) {}

  async read(graphId: string) {
    if (await this.primary.ping()) return this.primary.read(graphId);
    this.log.warn('mongo unreachable, reading from memory fallback', { graphId });
    return this.fallback.read(graphId);
  }

  async save(graphId: string, graph: Parameters<GraphRepository['save']>[1], options?: Parameters<GraphRepository['save']>[2]) {
    if (await this.primary.ping()) return this.primary.save(graphId, graph, options);
    this.log.warn('mongo unreachable, saving to memory fallback', { graphId });
    return this.fallback.save(graphId, graph, options);
  }

  ping(): Promise<boolean> {
    return this.primary.ping();
  }

  close(): Promise<void> {
    return this.primary.close();
  }
}

void main();
