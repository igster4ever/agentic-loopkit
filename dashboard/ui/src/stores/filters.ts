import { create } from 'zustand';

interface FilterState {
  stream:        string;
  eventType:     string;
  correlationId: string;
  since:         string;
}

interface FilterStore extends FilterState {
  setFilter: (key: keyof FilterState, value: string) => void;
  reset:     () => void;
}

const defaults: FilterState = {
  stream:        '',
  eventType:     '',
  correlationId: '',
  since:         '',
};

export const useFilterStore = create<FilterStore>((set) => ({
  ...defaults,
  setFilter: (key, value) => set((s) => ({ ...s, [key]: value })),
  reset:     () => set(defaults),
}));
