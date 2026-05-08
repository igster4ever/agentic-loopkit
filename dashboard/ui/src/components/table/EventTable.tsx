import type { FC } from 'react';
import type { Event } from '../../types/events';

// Source-type colour coding matches the spec node colour scheme
function sourceColour(event: Event): string {
  const t = event.event_type;
  const s = event.source.toLowerCase();
  if (t.startsWith('system.'))         return 'bg-zinc-700 text-zinc-300';
  if (s.includes('adapter'))           return 'bg-purple-900/60 text-purple-300';
  if (s.includes('agent'))             return 'bg-blue-900/60 text-blue-300';
  if (s.includes('executor') || s.includes('ralf') || s.includes('react') || s.includes('plan'))
                                       return 'bg-green-900/60 text-green-300';
  return 'bg-orange-900/60 text-orange-300';
}

interface EventTableProps {
  events:          Event[];
  onSelectChain?:  (correlationId: string) => void;
  onSelectEvent?:  (eventId: string) => void;
}

export const EventTable: FC<EventTableProps> = ({ events, onSelectChain, onSelectEvent }) => {
  if (events.length === 0) {
    return <p className="text-zinc-500 text-sm px-4 py-8 text-center">No events.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs text-left">
        <thead>
          <tr className="border-b border-zinc-800 text-zinc-500">
            <th className="px-3 py-2 font-medium">Timestamp</th>
            <th className="px-3 py-2 font-medium">Type</th>
            <th className="px-3 py-2 font-medium">Source</th>
            <th className="px-3 py-2 font-medium">Stream</th>
            <th className="px-3 py-2 font-medium">Correlation</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.event_id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
              <td className="px-3 py-2 text-zinc-400 whitespace-nowrap font-mono">
                {new Date(e.timestamp).toLocaleTimeString()}
              </td>
              <td className="px-3 py-2">
                <button
                  onClick={() => onSelectEvent?.(e.event_id)}
                  className="text-zinc-200 hover:text-white hover:underline text-left"
                >
                  {e.event_type}
                </button>
              </td>
              <td className="px-3 py-2">
                <span className={`px-1.5 py-0.5 rounded text-[11px] ${sourceColour(e)}`}>
                  {e.source}
                </span>
              </td>
              <td className="px-3 py-2 text-zinc-400">{e.stream}</td>
              <td className="px-3 py-2">
                {e.correlation_id ? (
                  <button
                    onClick={() => onSelectChain?.(e.correlation_id!)}
                    className="font-mono text-zinc-400 hover:text-blue-400 hover:underline truncate max-w-[120px] block"
                    title={e.correlation_id}
                  >
                    {e.correlation_id.slice(0, 8)}…
                  </button>
                ) : (
                  <span className="text-zinc-600">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
