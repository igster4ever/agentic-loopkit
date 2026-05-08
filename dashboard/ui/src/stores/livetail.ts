import { create } from 'zustand';
import type { Event } from '../types/events';

const MAX_BUFFER = 500;

interface LiveTailStore {
  events:    Event[];
  paused:    boolean;
  maxBuffer: number;
  push:      (event: Event) => void;
  setPaused: (paused: boolean) => void;
  clear:     () => void;
}

export const useLiveTailStore = create<LiveTailStore>((set) => ({
  events:    [],
  paused:    false,
  maxBuffer: MAX_BUFFER,

  push: (event) =>
    set((s) => {
      if (s.paused) return s;
      const events = [event, ...s.events];
      return { events: events.length > s.maxBuffer ? events.slice(0, s.maxBuffer) : events };
    }),

  setPaused: (paused) => set({ paused }),
  clear:     () => set({ events: [] }),
}));
