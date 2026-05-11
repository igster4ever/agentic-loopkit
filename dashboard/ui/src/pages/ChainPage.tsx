import type { FC } from 'react';
import { useEffect } from 'react';
import { useApi } from '../hooks/useApi';
import { EventChainGraph } from '../components/graph/EventChainGraph';
import { EventDetailPanel } from '../components/layout/EventDetailPanel';
import { useGraphStore } from '../stores/graph';
import type { EventChain } from '../types/events';

const STATUS_STYLES: Record<string, string> = {
  completed:   'bg-green-900/50 text-green-400',
  in_progress: 'bg-blue-900/50 text-blue-400',
  error:       'bg-red-900/50 text-red-400',
};

interface Props {
  correlationId: string;
  onNavigate: (href: string) => void;
}

export const ChainPage: FC<Props> = ({ correlationId, onNavigate }) => {
  const { data, loading, error } = useApi<EventChain>(`/api/chains/${correlationId}`);
  const { setChain, clear, selectedId } = useGraphStore();

  useEffect(() => {
    if (data) setChain(data);
    return () => { clear(); };
  }, [data, setChain, clear]);

  const status = data?.summary.status;
  const statusStyle = status ? (STATUS_STYLES[status] ?? STATUS_STYLES['error']) : '';

  return (
    <div className="flex flex-col h-full">
      {/* Breadcrumb / header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800 shrink-0">
        <button
          onClick={() => onNavigate('/events')}
          className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          ← Events
        </button>
        <span className="text-zinc-700 text-xs">/</span>
        <span className="text-xs font-mono text-zinc-400" title={correlationId}>
          chain:{correlationId.slice(0, 12)}…
        </span>
        {status && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${statusStyle}`}>
            {status}
          </span>
        )}
        <div className="ml-auto flex items-center gap-3 text-xs text-zinc-500">
          {data && (
            <>
              <span>{data.events.length} event{data.events.length !== 1 ? 's' : ''}</span>
              {data.summary.duration_ms > 0 && (
                <span>{data.summary.duration_ms.toLocaleString()} ms</span>
              )}
            </>
          )}
        </div>
      </div>

      {/* Loading / error states */}
      {loading && <p className="text-zinc-500 text-sm px-4 py-8">Loading chain…</p>}
      {error   && <p className="text-red-400  text-sm px-4 py-8">Error: {error}</p>}

      {/* Graph + detail panel */}
      {data && (
        <div className="flex flex-1 overflow-hidden">
          <div className="flex-1 min-w-0">
            <EventChainGraph chain={data} />
          </div>
          {selectedId && (
            <div className="w-80 shrink-0 overflow-hidden">
              <EventDetailPanel />
            </div>
          )}
        </div>
      )}
    </div>
  );
};
