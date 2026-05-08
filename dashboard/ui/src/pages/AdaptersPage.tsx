import type { FC } from 'react';
import { useApi } from '../hooks/useApi';
import type { AdapterInfo } from '../types/events';

function fmtTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString();
}

export const AdaptersPage: FC = () => {
  const { data, loading, error } = useApi<AdapterInfo[]>('/api/adapters');

  return (
    <div className="p-6">
      <h2 className="text-lg font-semibold text-zinc-100 mb-6">Registered Adapters</h2>

      {loading && <p className="text-zinc-500 text-sm">Loading…</p>}
      {error   && <p className="text-red-400 text-sm">Error: {error}</p>}

      {data && (
        <div className="grid gap-3">
          {data.length === 0 ? (
            <p className="text-zinc-500 text-sm">No adapters registered.</p>
          ) : (
            data.map((a) => (
              <div
                key={a.name}
                className="bg-zinc-900 border border-zinc-800 rounded-lg px-4 py-3"
              >
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-purple-400 font-medium text-sm">{a.name}</span>
                  <span className="text-xs bg-purple-900/40 text-purple-300 px-2 py-0.5 rounded">
                    {a.type}
                  </span>
                  {a.error_count > 0 && (
                    <span className="text-xs bg-red-900/40 text-red-300 px-2 py-0.5 rounded ml-auto">
                      {a.error_count} error{a.error_count !== 1 ? 's' : ''}
                    </span>
                  )}
                </div>
                <p className="text-zinc-500 text-xs">Last tick: {fmtTime(a.last_tick)}</p>
                {a.cursor && (
                  <p className="text-zinc-600 text-xs font-mono mt-0.5 truncate">
                    cursor: {a.cursor}
                  </p>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
};
