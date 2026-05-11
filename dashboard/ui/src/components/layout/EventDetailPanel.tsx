import type { FC, ReactNode } from 'react';
import { useState } from 'react';
import type { EventMeta } from '../../types/events';
import { useGraphStore } from '../../stores/graph';

// ── small helpers ─────────────────────────────────────────────────────────────

const Pill: FC<{ label: string; value: string }> = ({ label, value }) => (
  <span className="inline-flex items-center gap-1 text-[10px] rounded px-1.5 py-0.5 bg-zinc-800 text-zinc-400">
    <span className="text-zinc-500">{label}</span>
    <span className="text-zinc-200 font-mono">{value}</span>
  </span>
);

interface TabBtnProps { active: boolean; disabled?: boolean; onClick: () => void; children: ReactNode }
const TabBtn: FC<TabBtnProps> = ({ active, disabled, onClick, children }) => (
  <button
    onClick={onClick}
    disabled={disabled}
    className={[
      'px-4 py-2 text-xs font-medium transition-colors border-b-2',
      active
        ? 'border-blue-500 text-zinc-100'
        : 'border-transparent text-zinc-500 hover:text-zinc-300 disabled:opacity-30 disabled:cursor-default',
    ].join(' ')}
  >
    {children}
  </button>
);

// ── main component ────────────────────────────────────────────────────────────

export const EventDetailPanel: FC = () => {
  const { chain, selectedId, selectEvent } = useGraphStore();
  const [tab, setTab] = useState<'payload' | 'context'>('payload');

  const event = chain?.events.find((e) => e.event_id === selectedId) ?? null;

  if (!event) {
    return (
      <div className="flex flex-col h-full border-l border-zinc-800 items-center justify-center">
        <p className="text-zinc-600 text-sm">Select an event node to inspect</p>
      </div>
    );
  }

  const meta = event.payload['_meta'] as EventMeta | undefined;

  const displayPayload = Object.fromEntries(
    Object.entries(event.payload).filter(([k]) => k !== '_meta'),
  );

  const confidenceLabel = meta?.confidence !== undefined
    ? (meta.confidence >= 0.85 ? 'HIGH' : meta.confidence >= 0.65 ? 'MED' : meta.confidence >= 0.40 ? 'LOW' : 'REJECT')
    : undefined;

  return (
    <div className="flex flex-col h-full border-l border-zinc-800 bg-zinc-950">
      {/* Header */}
      <div className="px-4 py-3 border-b border-zinc-800 shrink-0 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs font-mono text-zinc-200 truncate">{event.event_type}</div>
          <div className="text-[11px] text-zinc-500 mt-0.5 font-mono truncate">
            {event.event_id.slice(0, 8)}… · {event.source}
          </div>
          <div className="text-[11px] text-zinc-600 mt-0.5">
            {new Date(event.timestamp).toLocaleString()}
          </div>
        </div>
        <button
          onClick={() => selectEvent(null)}
          className="text-zinc-600 hover:text-zinc-400 text-lg leading-none shrink-0 mt-0.5"
          aria-label="Close panel"
        >
          ×
        </button>
      </div>

      {/* EventMeta strip */}
      {meta && (
        <div className="px-4 py-2 border-b border-zinc-800 flex flex-wrap gap-1.5 shrink-0">
          {meta.phase      && <Pill label="phase" value={meta.phase} />}
          {meta.loop_type  && <Pill label="loop"  value={meta.loop_type} />}
          {meta.iteration  !== undefined && <Pill label="iter"  value={String(meta.iteration)} />}
          {meta.confidence !== undefined && <Pill label="conf"  value={`${meta.confidence.toFixed(2)} ${confidenceLabel}`} />}
          {meta.tags?.map((t) => <Pill key={t} label="tag" value={t} />)}
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-zinc-800 shrink-0">
        <TabBtn active={tab === 'payload'} onClick={() => setTab('payload')}>Payload</TabBtn>
        <TabBtn active={tab === 'context'} disabled={!meta?.context} onClick={() => setTab('context')}>
          Context
        </TabBtn>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {tab === 'payload' && (
          <pre className="text-[11px] font-mono text-zinc-300 p-3 leading-relaxed whitespace-pre-wrap break-all">
            {JSON.stringify(displayPayload, null, 2)}
          </pre>
        )}
        {tab === 'context' && meta?.context && (
          <div className="p-4 text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
            {meta.context}
          </div>
        )}
      </div>
    </div>
  );
};
