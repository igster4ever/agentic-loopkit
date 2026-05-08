import type { FC } from 'react';
import { useState } from 'react';
import { useApi } from '../hooks/useApi';
import { EventTable } from '../components/table/EventTable';
import type { EventsResponse } from '../types/events';

export const EventsPage: FC<{ onNavigate: (href: string) => void }> = ({ onNavigate }) => {
  const [stream, setStream] = useState('');
  const [limit]             = useState(100);

  const params = new URLSearchParams();
  if (stream) params.set('stream', stream);
  params.set('limit', String(limit));

  const { data, loading, error, refetch } =
    useApi<EventsResponse>(`/api/events?${params.toString()}`, [stream]);

  return (
    <div className="flex flex-col h-full">
      {/* Filter bar */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-800 shrink-0">
        <input
          type="text"
          placeholder="Stream filter…"
          value={stream}
          onChange={(e) => setStream(e.target.value)}
          className="bg-zinc-800 border border-zinc-700 text-zinc-200 text-sm rounded px-3 py-1.5 w-44 placeholder-zinc-500 focus:outline-none focus:border-zinc-500"
        />
        <button
          onClick={refetch}
          className="text-xs px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors"
        >
          ↻ Refresh
        </button>
        {data && (
          <span className="ml-auto text-xs text-zinc-500">
            {data.total} event{data.total !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      <div className="flex-1 overflow-auto">
        {loading && <p className="text-zinc-500 text-sm px-4 py-8">Loading…</p>}
        {error   && <p className="text-red-400 text-sm px-4 py-8">Error: {error}</p>}
        {data && (
          <EventTable
            events={data.events}
            onSelectChain={(id) => onNavigate(`/chains/${id}`)}
          />
        )}
      </div>
    </div>
  );
};
