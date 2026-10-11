/**
 * Structured errors. Every failure crossing the HTTP boundary carries a stable `code` plus the node
 * and edge ids it refers to, so the UI can highlight exactly what broke.
 */
export interface ErrorBody {
  code: string;
  message: string;
  nodeIds: string[];
  edgeIds: string[];
  details?: Record<string, unknown>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly nodeIds: string[];
  readonly edgeIds: string[];
  readonly details?: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    options: { nodeIds?: string[]; edgeIds?: string[]; details?: Record<string, unknown> } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.nodeIds = options.nodeIds ?? [];
    this.edgeIds = options.edgeIds ?? [];
    this.details = options.details;
  }

  toBody(): ErrorBody {
    return {
      code: this.code,
      message: this.message,
      nodeIds: this.nodeIds,
      edgeIds: this.edgeIds,
      ...(this.details ? { details: this.details } : {}),
    };
  }
}

export const ErrorCodes = {
  validationFailed: 'VALIDATION_FAILED',
  graphSchemaInvalid: 'GRAPH_SCHEMA_INVALID',
  simulationUnavailable: 'SIMULATION_SERVICE_UNAVAILABLE',
  simulationError: 'NGSPICE_ERROR',
  simulationTimeout: 'NGSPICE_TIMEOUT',
  dbUnavailable: 'DB_UNAVAILABLE',
  notFound: 'NOT_FOUND',
  badRequest: 'BAD_REQUEST',
} as const;

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}
