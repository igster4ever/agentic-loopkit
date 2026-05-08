// Mirrors Python Event dataclass exactly

export interface Event {
  event_id:       string;
  event_type:     string;
  stream:         string;
  source:         string;
  timestamp:      string;   // ISO 8601 UTC
  payload:        Record<string, unknown>;
  causation_id:   string | null;
  correlation_id: string | null;
}

export interface ChainEdge {
  from: string;   // event_id
  to:   string;   // event_id
}

export interface ChainSummary {
  stream_count:  number;
  agent_count:   number;
  ralf_loops:    number;
  ooda_events:   number;
  error_count:   number;
  duration_ms:   number;
  status:        'completed' | 'in_progress' | 'error';
}

export interface EventChain {
  correlation_id: string;
  events:         Event[];
  edges:          ChainEdge[];
  summary:        ChainSummary;
}

export interface StreamInfo {
  stream:        string;
  event_count:   number;
  last_event_at: string | null;
}

export interface EventsResponse {
  events: Event[];
  total:  number;
  limit:  number;
}

export interface AgentInfo {
  name:    string;
  type:    string;
  streams: string[];
}

export interface AdapterInfo {
  name:        string;
  type:        string;
  cursor:      string | null;
  last_tick:   string | null;
  error_count: number;
}

// EventMeta — optional framework metadata from payload["_meta"]
export interface EventMeta {
  phase?:      string;
  loop_type?:  'ooda' | 'ralf' | 'react' | 'plan' | 'reflexion' | 'outcome';
  iteration?:  number;
  confidence?: number;
  context?:    string;
  tags?:       string[];
}
