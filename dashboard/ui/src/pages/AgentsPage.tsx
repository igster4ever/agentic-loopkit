import type { FC } from 'react';
import { useApi } from '../hooks/useApi';
import type { AgentInfo } from '../types/events';

export const AgentsPage: FC = () => {
  const { data, loading, error } = useApi<AgentInfo[]>('/api/agents');

  return (
    <div className="p-6">
      <h2 className="text-lg font-semibold text-zinc-100 mb-6">Registered Agents</h2>

      {loading && <p className="text-zinc-500 text-sm">Loading…</p>}
      {error   && <p className="text-red-400 text-sm">Error: {error}</p>}

      {data && (
        <div className="grid gap-3">
          {data.length === 0 ? (
            <p className="text-zinc-500 text-sm">No agents registered.</p>
          ) : (
            data.map((a) => (
              <div
                key={a.name}
                className="bg-zinc-900 border border-zinc-800 rounded-lg px-4 py-3"
              >
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-blue-400 font-medium text-sm">{a.name}</span>
                  <span className="text-xs bg-blue-900/40 text-blue-300 px-2 py-0.5 rounded">
                    {a.type}
                  </span>
                </div>
                <p className="text-zinc-500 text-xs">
                  Streams: {a.streams.length ? a.streams.join(', ') : '—'}
                </p>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
};
