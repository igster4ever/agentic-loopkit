import { create } from 'zustand';
import type { EventChain } from '../types/events';

interface GraphStore {
  chain:       EventChain | null;
  selectedId:  string | null;
  setChain:    (chain: EventChain) => void;
  selectEvent: (id: string | null) => void;
  clear:       () => void;
}

export const useGraphStore = create<GraphStore>((set) => ({
  chain:       null,
  selectedId:  null,
  setChain:    (chain) => set({ chain, selectedId: null }),
  selectEvent: (id)    => set({ selectedId: id }),
  clear:       ()      => set({ chain: null, selectedId: null }),
}));
