/** MongoDB connection with retries so `docker compose up` survives a slow Mongo start. */
import { MongoClient } from 'mongodb';
import type { ApiConfig } from '../config.js';
import type { Logger } from '../logger.js';

export interface MongoConnection {
  client: MongoClient;
  dbName: string;
}

export async function connectMongo(config: ApiConfig, log: Logger): Promise<MongoConnection> {
  const client = new MongoClient(config.mongoUrl, {
    serverSelectionTimeoutMS: config.mongoServerSelectionTimeoutMs,
    connectTimeoutMS: config.mongoServerSelectionTimeoutMs,
  });

  let lastError: unknown;
  for (let attempt = 1; attempt <= config.mongoConnectRetries; attempt += 1) {
    try {
      await client.connect();
      await client.db(config.mongoDbName).command({ ping: 1 });
      log.info('mongo connected', { attempt, db: config.mongoDbName });
      return { client, dbName: config.mongoDbName };
    } catch (error) {
      lastError = error;
      log.warn('mongo connection failed', { attempt, retries: config.mongoConnectRetries, error: (error as Error).message });
      if (attempt < config.mongoConnectRetries) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(1000 * attempt, 3000)));
      }
    }
  }
  throw new Error(`could not connect to MongoDB at ${config.mongoUrl}: ${(lastError as Error)?.message ?? 'unknown error'}`);
}
