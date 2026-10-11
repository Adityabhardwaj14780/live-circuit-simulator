/**
 * Local development MongoDB: starts an in-memory mongod on a fixed port so the API can be run
 * without Docker. `docker compose up` uses the real mongo service instead — this script is only a
 * convenience for `npm run dev` on a workstation.
 *
 *   node scripts/dev-mongo.mjs [port]
 */
import { MongoMemoryServer } from 'mongodb-memory-server';

const port = Number.parseInt(process.argv[2] ?? '27017', 10);

const server = await MongoMemoryServer.create({
  instance: { port, dbName: 'live_node_graph', ip: '127.0.0.1' },
});

console.log(`[dev-mongo] listening on mongodb://127.0.0.1:${port} (db: live_node_graph)`);
console.log('[dev-mongo] press Ctrl+C to stop');

const shutdown = async (signal) => {
  console.log(`[dev-mongo] ${signal} received, stopping`);
  await server.stop();
  process.exit(0);
};
process.on('SIGINT', () => void shutdown('SIGINT'));
process.on('SIGTERM', () => void shutdown('SIGTERM'));
