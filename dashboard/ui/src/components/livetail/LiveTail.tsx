import type { FC } from 'react';
import { useCallback } from 'react';
import { useLiveTailStore } from '../../stores/livetail';
import { useEventStream } from '../../hooks/useEventStream';
import type { Event } from '../../types/events';

export const LiveTail: FC = () => {
  const { events, paused, push, setPaused, clear } = useLiveTailStore();

  const onEvent = useCallback((e: Event) => push(e), [push]);
  const { connected, reconnecting } = useEventStream({}, onEvent);

  const statusColour = connected
    ? 'bg-green-500'
    : reconnecting
    ? 'bg-yellow-500 animate-pulse'
    : 'bg-red-500';

  const statusLabel = connected ? 'Connected' : reconnecting ? 'Reconnecting…' : 'Disconnected';

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-800 shrink-0">
        <span className="flex items-center gap-1.5 text-sm text-zinc-400">
          <span className={`w-2 h-2 rounded-full ${statusColour}`} />
          {statusLabel}
        </span>
        <span className="ml-auto flex gap-2">
          <button
            onClick={() => setPaused(!paused)}
            className="text-xs px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors"
          >
            {paused ? '▶ Resume' : '⏸ Pause'}
          </button>
          <button
            onClick={clear}
            className="text-xs px-3 py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors"
          >
            Clear
          </button>
        </span>
      </div>

      {/* Event stream */}
      <div className="flex-1 overflow-y-auto font-mono text-xs p-3 space-y-1">
        {events.length === 0 ? (
          <p className="text-zinc-600 text-center py-12">
            {connected ? 'Waiting for events…' : 'Not connected.'}
          </p>
        ) : (
          events.map((e) => (
            <div key={e.event_id} className="flex gap-3 hover:bg-zinc-800/40 px-2 py-0.5 rounded">
              <span className="text-zinc-600 shrink-0">
                {new Date(e.timestamp).toLocaleTimeString()}
              </span>
              <span className="text-zinc-400 shrink-0">{e.stream}</span>
              <span className="text-zinc-200">{e.event_type}</span>
              <span className="text-zinc-500 shrink-0">{e.source}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
