/**
 * Environment configuration. Every value has a safe default so the API boots in tests, and every
 * value can be overridden by environment variables (see .env.example — placeholders only).
 */
export interface ApiConfig {
  port: number;
  nodeEnv: string;
  mongoUrl: string;
  mongoDbName: string;
  mongoServerSelectionTimeoutMs: number;
  mongoConnectRetries: number;
  simulationServiceUrl: string;
  simulationTimeoutMs: number;
  corsOrigins: string[] | '*';
  defaultGraphId: string;
  logLevel: 'debug' | 'info' | 'warn' | 'error';
}

function intFromEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw || raw.trim() === '') return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): ApiConfig {
  const origins = (env.CORS_ORIGINS ?? '*').trim();
  return {
    port: intFromEnv('API_PORT', 4000),
    nodeEnv: env.NODE_ENV ?? 'development',
    mongoUrl: env.MONGO_URL ?? 'mongodb://localhost:27017',
    mongoDbName: env.MONGO_DB_NAME ?? 'live_node_graph',
    mongoServerSelectionTimeoutMs: intFromEnv('MONGO_SERVER_SELECTION_TIMEOUT_MS', 5000),
    mongoConnectRetries: intFromEnv('MONGO_CONNECT_RETRIES', 10),
    simulationServiceUrl: (env.SIMULATION_SERVICE_URL ?? 'http://localhost:8000').replace(/\/+$/, ''),
    simulationTimeoutMs: intFromEnv('SIMULATION_TIMEOUT_MS', 15000),
    corsOrigins: origins === '*' ? '*' : origins.split(',').map((value) => value.trim()).filter(Boolean),
    defaultGraphId: env.DEFAULT_GRAPH_ID ?? 'default',
    logLevel: (env.LOG_LEVEL as ApiConfig['logLevel']) ?? 'info',
  };
}

export const config = loadConfig();
