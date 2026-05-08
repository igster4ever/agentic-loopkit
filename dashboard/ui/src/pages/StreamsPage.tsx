import type { FC } from 'react';
import { useApi } from '../hooks/useApi';
import type { StreamInfo } from '../types/events';

function fmtTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString();
}

export const StreamsPage: FC = () => {
  const { data, loading, error, refetch } = useApi<StreamInfo[]>('/api/streams');

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-semibold text-zinc-100">Event Streams</h2>
        <button
          onClick={refetch}
          className="text-xs px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors"
        >
          ↻ Refresh
        </button>
      </div>

      {loading && <p className="text-zinc-500 text-sm">Loading…</p>}
      {error   && <p className="text-red-400 text-sm">Error: {error}</p>}

      {data && (
        <div className="grid gap-3">
          {data.length === 0 ? (
            <p className="text-zinc-500 text-sm">No streams yet — publish some events first.</p>
          ) : (
            data.map((s) => (
              <div
                key={s.stream}
                className="flex items-center justify-between bg-zinc-900 border border-zinc-800 rounded-lg px-4 py-3"
              >
                <div>
                  <p className="text-zinc-100 font-medium text-sm">{s.stream}</p>
                  <p className="text-zinc-500 text-xs mt-0.5">Last event: {fmtTime(s.last_event_at)}</p>
                </div>
                <span className="text-2xl font-bold text-zinc-300 tabular-nums">
                  {s.event_count.toLocaleString()}
                </span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
};
