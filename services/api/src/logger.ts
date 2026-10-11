/** Minimal structured logger (JSON lines) — no extra dependency, easy to grep in `docker compose logs`. */
type Level = 'debug' | 'info' | 'warn' | 'error';

const ORDER: Record<Level, number> = { debug: 10, info: 20, warn: 30, error: 40 };

export interface Logger {
  debug(message: string, fields?: Record<string, unknown>): void;
  info(message: string, fields?: Record<string, unknown>): void;
  warn(message: string, fields?: Record<string, unknown>): void;
  error(message: string, fields?: Record<string, unknown>): void;
  child(bindings: Record<string, unknown>): Logger;
}

export function createLogger(level: Level = 'info', bindings: Record<string, unknown> = {}): Logger {
  const threshold = ORDER[level] ?? ORDER.info;

  const emit = (entryLevel: Level, message: string, fields?: Record<string, unknown>): void => {
    if (ORDER[entryLevel] < threshold) return;
    const line = JSON.stringify({
      ts: new Date().toISOString(),
      level: entryLevel,
      msg: message,
      ...bindings,
      ...fields,
    });
    if (entryLevel === 'error') process.stderr.write(`${line}\n`);
    else process.stdout.write(`${line}\n`);
  };

  return {
    debug: (message, fields) => emit('debug', message, fields),
    info: (message, fields) => emit('info', message, fields),
    warn: (message, fields) => emit('warn', message, fields),
    error: (message, fields) => emit('error', message, fields),
    child: (extra) => createLogger(level, { ...bindings, ...extra }),
  };
}

export const logger = createLogger();
