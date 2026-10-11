import { NETLIST_HINTS } from '@lng/graph-contract';
import type { LiveResult, LiveSnapshot } from '../engine/liveSync';

/**
 * Right-hand panel: what the backend did with the current graph — netlist, ngspice timings,
 * per-node errors and structured warnings. This is where "which node broke and why" is answered.
 */
export function Inspector({ snapshot }: { snapshot: LiveSnapshot }) {
  const result: LiveResult | null = snapshot.result;
  const error = snapshot.error;
  const failures = (result?.results ?? []).filter((entry) => entry.status !== 'ok');

  return (
    <aside className="inspector">
      <h2>Simulation</h2>

      {error && (
        <div className="banner banner--error" data-testid="sim-error">
          <strong>{error.code}</strong>
          <span>{error.message}</span>
          {error.nodeIds.length > 0 && <span>nodes: {error.nodeIds.join(', ')}</span>}
        </div>
      )}

      {result && (
        <>
          <dl className="kv">
            <dt>status</dt>
            <dd>{result.status}</dd>
            <dt>revision</dt>
            <dd>{result.revision}</dd>
            <dt>ngspice</dt>
            <dd>{result.ngspiceVersion ?? 'unknown'}</dd>
            <dt>timings</dt>
            <dd>
              validate {result.timings.validationMs} ms · netlist {result.timings.netlistMs} ms · ngspice{' '}
              {result.timings.ngspiceMs} ms · parse {result.timings.parseMs} ms
            </dd>
            {result.topology.cyclic && result.topology.cyclic.length > 0 && (
              <>
                <dt>cycles</dt>
                <dd className="danger">{result.topology.cyclic.join(', ')}</dd>
              </>
            )}
          </dl>

          {failures.length > 0 && (
            <section>
              <h3>Node problems</h3>
              <ul className="problems" data-testid="problems">
                {failures.map((entry) => (
                  <li key={entry.nodeId}>
                    <code>{entry.nodeId}</code> <em>{entry.errorCode}</em>
                    <span>{entry.message}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {(result.warnings ?? []).length > 0 && (
            <section>
              <h3>Warnings</h3>
              <ul className="warnings" data-testid="warnings">
                {result.warnings.map((warning, index) => (
                  <li key={`${warning.code}-${index}`}>
                    <code>{warning.code}</code> <span>{warning.message}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h3>Generated netlist</h3>
            <pre data-testid="netlist">{result.netlist ?? '(not generated)'}</pre>
          </section>
        </>
      )}

      {!result && !error && <p className="muted">Edit the graph — the netlist and values appear here automatically.</p>}

      <section>
        <h3>Node → netlist mapping</h3>
        <table className="mapping">
          <tbody>
            {Object.values(NETLIST_HINTS).map((hint) => (
              <tr key={hint.nodeType}>
                <td>{hint.nodeType}</td>
                <td>
                  <code>{hint.template}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </aside>
  );
}
