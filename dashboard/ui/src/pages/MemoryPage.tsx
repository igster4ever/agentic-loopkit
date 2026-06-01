import { useState } from 'react';
import type { FC } from 'react';
import { useApi } from '../hooks/useApi';

interface MemoryRecord {
  memory_id:       string;
  key:             string;
  value:           string;
  agent_id:        string;
  confidence:      number;
  tags:            string[];
  source_event_id: string | null;
  correlation_id:  string | null;
  created_at:      string;
  updated_at:      string;
  expires_at:      string | null;
  valid:           boolean;
  version:         number;
}

interface MemoryOperation {
  op:         'ADD' | 'UPDATE' | 'DELETE' | 'NOOP';
  memory_id:  string;
  agent_id:   string;
  old_value:  string | null;
  new_value:  string | null;
  reason:     string;
  timestamp:  string;
}

interface FactsResponse {
  facts: MemoryRecord[];
  total: number;
  limit: number;
}

interface HistoryResponse {
  agent_id:  string;
  key:       string;
  memory_id: string;
  history:   MemoryOperation[];
  versions:  number;
}

const OP_COLOURS: Record<string, string> = {
  ADD:    'bg-green-900/40 text-green-300',
  UPDATE: 'bg-blue-900/40  text-blue-300',
  DELETE: 'bg-red-900/40   text-red-300',
  NOOP:   'bg-zinc-800     text-zinc-400',
};

const FactRow: FC<{
  record:   MemoryRecord;
  onSelect: (r: MemoryRecord) => void;
  selected: boolean;
}> = ({ record, onSelect, selected }) => (
  <div
    onClick={() => onSelect(record)}
    className={[
      'bg-zinc-900 border rounded-lg px-4 py-3 cursor-pointer transition-colors',
      selected ? 'border-blue-600' : 'border-zinc-800 hover:border-zinc-600',
    ].join(' ')}
  >
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-blue-400 font-medium text-sm truncate">{record.key}</span>
          <span className="text-xs text-zinc-500 shrink-0">v{record.version}</span>
          {record.tags.map(t => (
            <span key={t} className="text-xs bg-zinc-800 text-zinc-400 px-1.5 py-0.5 rounded">
              {t}
            </span>
          ))}
        </div>
        <p className="text-zinc-300 text-sm break-words">{record.value}</p>
      </div>
      <div className="text-right shrink-0">
        <span className="text-xs text-zinc-500">{record.agent_id}</span>
        <div className="text-xs text-zinc-600 mt-0.5">
          {Math.round(record.confidence * 100)}% conf
        </div>
      </div>
    </div>
  </div>
);

const HistoryPanel: FC<{ record: MemoryRecord; onClose: () => void }> = ({ record, onClose }) => {
  const url = `/api/memory/${encodeURIComponent(record.agent_id)}/${encodeURIComponent(record.key)}/history`;
  const { data, loading, error } = useApi<HistoryResponse>(url);

  return (
    <div className="w-96 shrink-0 bg-zinc-900 border-l border-zinc-800 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
        <div>
          <p className="text-sm font-medium text-zinc-100 truncate">{record.key}</p>
          <p className="text-xs text-zinc-500">{record.agent_id}</p>
        </div>
        <button
          onClick={onClose}
          className="text-zinc-500 hover:text-zinc-200 text-lg leading-none px-1"
        >
          ×
        </button>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-3">
        {loading && <p className="text-zinc-500 text-sm">Loading history…</p>}
        {error   && <p className="text-red-400 text-sm">Error: {error}</p>}

        {data && data.history.length === 0 && (
          <p className="text-zinc-500 text-sm">No operations recorded.</p>
        )}

        {data?.history.map((op, i) => (
          <div key={i} className="bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2">
            <div className="flex items-center gap-2 mb-1">
              <span className={`text-xs px-1.5 py-0.5 rounded font-mono ${OP_COLOURS[op.op] ?? 'bg-zinc-800 text-zinc-400'}`}>
                {op.op}
              </span>
              <span className="text-xs text-zinc-500">
                {new Date(op.timestamp).toLocaleString()}
              </span>
            </div>
            {op.new_value !== null && (
              <p className="text-zinc-300 text-xs break-words mt-1">{op.new_value}</p>
            )}
            {op.old_value !== null && op.op === 'UPDATE' && (
              <p className="text-zinc-600 text-xs break-words line-through mt-0.5">{op.old_value}</p>
            )}
            <p className="text-zinc-600 text-xs mt-1 italic">{op.reason}</p>
          </div>
        ))}
      </div>
    </div>
  );
};

export const MemoryPage: FC = () => {
  const [selectedAgent, setSelectedAgent] = useState<string>('');
  const [selectedRecord, setSelectedRecord] = useState<MemoryRecord | null>(null);

  const url = selectedAgent ? `/api/memory/${encodeURIComponent(selectedAgent)}` : '/api/memory';
  const { data, loading, error, refetch } = useApi<FactsResponse>(url);

  const agents = Array.from(new Set((data?.facts ?? []).map(f => f.agent_id))).sort();

  const handleSelect = (r: MemoryRecord) => {
    setSelectedRecord(prev => (prev?.memory_id === r.memory_id ? null : r));
  };

  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex-1 overflow-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-lg font-semibold text-zinc-100">Memory</h2>
            {data && (
              <p className="text-xs text-zinc-500 mt-0.5">
                {data.total} fact{data.total !== 1 ? 's' : ''}
                {selectedAgent ? ` · ${selectedAgent}` : ' · all agents'}
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            <select
              value={selectedAgent}
              onChange={e => { setSelectedAgent(e.target.value); setSelectedRecord(null); }}
              className="bg-zinc-800 border border-zinc-700 text-zinc-200 text-sm rounded px-2 py-1"
            >
              <option value="">All agents</option>
              {agents.map(a => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
            <button
              onClick={refetch}
              className="text-xs text-zinc-400 hover:text-zinc-200 bg-zinc-800 hover:bg-zinc-700 px-3 py-1 rounded transition-colors"
            >
              Refresh
            </button>
          </div>
        </div>

        {loading && <p className="text-zinc-500 text-sm">Loading…</p>}
        {error   && <p className="text-red-400 text-sm">Error: {error}</p>}

        {data && (
          <div className="space-y-2">
            {data.facts.length === 0 ? (
              <p className="text-zinc-500 text-sm">No facts found.</p>
            ) : (
              data.facts.map(r => (
                <FactRow
                  key={r.memory_id}
                  record={r}
                  onSelect={handleSelect}
                  selected={selectedRecord?.memory_id === r.memory_id}
                />
              ))
            )}
          </div>
        )}
      </div>

      {selectedRecord && (
        <HistoryPanel
          record={selectedRecord}
          onClose={() => setSelectedRecord(null)}
        />
      )}
    </div>
  );
};
