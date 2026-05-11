import type { FC, MouseEvent } from 'react';
import { useMemo, useCallback } from 'react';
import {
  ReactFlow, Background, Controls,
  type Node, type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import type { EventChain, Event } from '../../types/events';
import { useGraphStore } from '../../stores/graph';

const NODE_W = 224;
const NODE_H = 60;

function nodePalette(event: Event): { bg: string; accent: string } {
  const t = event.event_type;
  const s = event.source.toLowerCase();
  if (t.startsWith('system.'))                                               return { bg: '#27272a', accent: '#a1a1aa' };
  if (s.includes('adapter'))                                                 return { bg: '#2e1065', accent: '#c084fc' };
  if (s.includes('agent'))                                                   return { bg: '#1e3a5f', accent: '#93c5fd' };
  if (s.includes('executor') || s.includes('ralf') || s.includes('react') || s.includes('plan'))
                                                                             return { bg: '#14532d', accent: '#86efac' };
  return { bg: '#431407', accent: '#fdba74' };
}

function dagreLayout(nodes: Node[], edges: Edge[]): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: 44, ranksep: 70 });

  nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  edges.forEach((e) => g.setEdge(e.source, e.target));

  dagre.layout(g);

  return {
    nodes: nodes.map((n) => {
      const { x, y } = g.node(n.id);
      return { ...n, position: { x: x - NODE_W / 2, y: y - NODE_H / 2 } };
    }),
    edges,
  };
}

function buildNodes(events: Event[]): Node[] {
  return events.map((e) => {
    const { bg, accent } = nodePalette(e);
    const meta = e.payload['_meta'] as { phase?: string; loop_type?: string } | undefined;
    const sub = [e.source, meta?.phase, meta?.loop_type ? `(${meta.loop_type})` : undefined]
      .filter(Boolean).join(' · ');

    return {
      id: e.event_id,
      type: 'default' as const,
      position: { x: 0, y: 0 },
      style: {
        background: bg,
        color: accent,
        border: '1px solid #3f3f46',
        borderRadius: 6,
        padding: '6px 10px',
        width: NODE_W,
        fontSize: 11,
        cursor: 'pointer',
      },
      data: {
        label: (
          <div style={{ textAlign: 'left' }}>
            <div style={{ fontFamily: 'monospace', fontWeight: 600, color: accent, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {e.event_type}
            </div>
            <div style={{ color: '#71717a', fontSize: 10, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {sub}
            </div>
          </div>
        ),
      },
    };
  });
}

interface Props { chain: EventChain }

export const EventChainGraph: FC<Props> = ({ chain }) => {
  const { selectedId, selectEvent } = useGraphStore();

  const { nodes: baseNodes, edges } = useMemo(() => {
    const rawNodes = buildNodes(chain.events);
    const rawEdges: Edge[] = chain.edges.map((ce, i) => ({
      id: `e-${i}`,
      source: ce.from,
      target: ce.to,
      style: { stroke: '#52525b', strokeWidth: 1.5 },
    }));
    return dagreLayout(rawNodes, rawEdges);
  }, [chain]);

  const nodes = useMemo(
    () => baseNodes.map((n) => ({
      ...n,
      selected: n.id === selectedId,
      style: {
        ...n.style,
        border: n.id === selectedId ? '1.5px solid #60a5fa' : '1px solid #3f3f46',
        boxShadow: n.id === selectedId ? '0 0 0 2px #1d4ed8' : undefined,
      },
    })),
    [baseNodes, selectedId],
  );

  const onNodeClick = useCallback(
    (_: MouseEvent, node: Node) => selectEvent(node.id),
    [selectEvent],
  );

  return (
    <div style={{ width: '100%', height: '100%' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodeClick={onNodeClick}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        nodesDraggable={false}
        nodesConnectable={false}
        colorMode="dark"
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#27272a" gap={20} />
        <Controls position="bottom-right" showInteractive={false} />
      </ReactFlow>
    </div>
  );
};
