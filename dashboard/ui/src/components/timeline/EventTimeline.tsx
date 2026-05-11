import type { FC } from 'react';
import { useMemo } from 'react';
import {
  ScatterChart, Scatter, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import { useApi } from '../../hooks/useApi';
import type { EventsResponse } from '../../types/events';

interface Point { x: number; y: number; event_type: string; stream: string }

interface TooltipPayloadItem { value: number }
interface CustomTooltipProps {
  active?: boolean;
  payload?: TooltipPayloadItem[];
  points?: Point[];
}

const CustomTooltip: FC<CustomTooltipProps> = ({ active, payload, points }) => {
  if (!active || !payload?.length || !points) return null;
  const xVal = payload[0]?.value;
  const yVal = payload[1]?.value;
  if (xVal === undefined || yVal === undefined) return null;
  const pt = points.find((p) => p.x === xVal && p.y === yVal);
  if (!pt) return null;
  return (
    <div className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-[11px]">
      <div className="text-zinc-200 font-mono">{pt.event_type}</div>
      <div className="text-zinc-500 mt-0.5">{new Date(pt.x).toLocaleTimeString()}</div>
    </div>
  );
};

export const EventTimeline: FC = () => {
  const { data, loading, error } = useApi<EventsResponse>('/api/events?limit=300');

  const { points, streamNames, domain } = useMemo(() => {
    if (!data?.events.length) return { points: [], streamNames: [], domain: [0, 0] as [number, number] };

    const names = [...new Set(data.events.map((e) => e.stream))].sort();
    const streamIndex = Object.fromEntries(names.map((s, i) => [s, i]));

    const pts: Point[] = data.events.map((e) => ({
      x: new Date(e.timestamp).getTime(),
      y: streamIndex[e.stream] ?? 0,
      event_type: e.event_type,
      stream: e.stream,
    }));

    const timestamps = pts.map((p) => p.x);
    return {
      points: pts,
      streamNames: names,
      domain: [Math.min(...timestamps), Math.max(...timestamps)] as [number, number],
    };
  }, [data]);

  if (loading) return <p className="text-zinc-500 text-sm px-4 py-6">Loading timeline…</p>;
  if (error)   return <p className="text-red-400 text-sm px-4 py-6">Timeline error: {error}</p>;
  if (!points.length) return null;

  return (
    <div className="mt-8">
      <h3 className="text-xs font-medium text-zinc-500 uppercase tracking-widest mb-3 px-1">
        Event Timeline
      </h3>
      <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
        <ResponsiveContainer width="100%" height={Math.max(120, streamNames.length * 40 + 40)}>
          <ScatterChart margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
            <XAxis
              dataKey="x"
              type="number"
              domain={domain}
              tickCount={6}
              tickFormatter={(v: number) => new Date(v).toLocaleTimeString()}
              tick={{ fill: '#71717a', fontSize: 10 }}
              axisLine={{ stroke: '#3f3f46' }}
              tickLine={false}
            />
            <YAxis
              dataKey="y"
              type="number"
              domain={[-0.5, streamNames.length - 0.5]}
              ticks={streamNames.map((_, i) => i)}
              tickFormatter={(i: number) => streamNames[Math.round(i)] ?? ''}
              tick={{ fill: '#71717a', fontSize: 10 }}
              axisLine={{ stroke: '#3f3f46' }}
              tickLine={false}
              width={80}
            />
            <Tooltip content={<CustomTooltip points={points} />} cursor={{ stroke: '#52525b' }} />
            <Scatter data={points} fill="#60a5fa" opacity={0.7} r={3} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
