import type { HealthResponse } from '../api/client';
import type { LiveSnapshot } from '../engine/liveSync';

export function StatusBar({
  snapshot,
  health,
  graphRevision,
  persistedAt,
}: {
  snapshot: LiveSnapshot;
  health: HealthResponse | null;
  graphRevision: number | null;
  persistedAt: string | null;
}) {
  const label: Record<LiveSnapshot['phase'], string> = {
    idle: 'idle',
    stale: 'edited — waiting for the next run',
    running: 'simulating…',
    ok: 'live: simulation ok',
    partial: 'live: partial result',
    error: 'live: error',
  };

  return (
    <footer className="statusbar">
      <span className={`chip chip--${snapshot.phase}`} data-testid="live-phase">
        {label[snapshot.phase]}
      </span>
      <span className="statusbar__item">
        run #{snapshot.runs} · revision {snapshot.revision || '—'} / requested {snapshot.requestedRevision || '—'}
      </span>
      {snapshot.lastDurationMs !== null && <span className="statusbar__item">last run {Math.round(snapshot.lastDurationMs)} ms</span>}
      <span className="statusbar__item">
        ngspice: {health?.simulation?.ngspice?.available ? 'available' : health ? 'unavailable' : 'unknown'}
      </span>
      <span className="statusbar__item">mongo: {health?.mongo?.ok ? 'ok' : health ? 'down' : 'unknown'}</span>
      {graphRevision !== null && (
        <span className="statusbar__item" data-testid="persisted-revision">
          saved revision {graphRevision}
          {persistedAt ? ` · ${new Date(persistedAt).toLocaleTimeString()}` : ''}
        </span>
      )}
    </footer>
  );
}
