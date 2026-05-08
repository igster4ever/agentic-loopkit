import { useEffect, useRef, useState } from 'react';
import type { Event } from '../types/events';

interface UseEventStreamParams {
  stream?:    string;
  eventType?: string;
}

interface UseEventStreamResult {
  connected:    boolean;
  reconnecting: boolean;
}

const BASE_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS  = 30_000;

export function useEventStream(
  { stream, eventType }: UseEventStreamParams,
  onEvent: (event: Event) => void,
): UseEventStreamResult {
  const [connected, setConnected]       = useState(false);
  const [reconnecting, setReconnecting] = useState(false);

  // Stable ref to latest onEvent — avoids re-connecting on every render
  const onEventRef = useRef(onEvent);
  useEffect(() => { onEventRef.current = onEvent; }, [onEvent]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let backoff = BASE_BACKOFF_MS;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    function connect() {
      if (stopped) return;

      const params = new URLSearchParams();
      if (stream)    params.set('stream', stream);
      if (eventType) params.set('event_type', eventType);
      const query = params.toString();
      const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/tail${query ? `?${query}` : ''}`;

      ws = new WebSocket(url);

      ws.onopen = () => {
        setConnected(true);
        setReconnecting(false);
        backoff = BASE_BACKOFF_MS;
      };

      ws.onmessage = (e: MessageEvent<string>) => {
        try {
          const event = JSON.parse(e.data) as Event;
          onEventRef.current(event);
        } catch {
          // malformed frame — ignore
        }
      };

      ws.onclose = () => {
        setConnected(false);
        if (!stopped) {
          setReconnecting(true);
          retryTimer = setTimeout(() => {
            backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
            connect();
          }, backoff);
        }
      };

      ws.onerror = () => {
        ws?.close();
      };
    }

    connect();

    return () => {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      ws?.close();
      setConnected(false);
      setReconnecting(false);
    };
  }, [stream, eventType]);

  return { connected, reconnecting };
}
