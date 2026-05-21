'use client';

import { useState, useMemo, useCallback, useRef } from 'react';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface TraceEvent {
  tick: number;
  kind: 'start' | 'stop' | 'send' | 'recv' | 'bid' | 'ask' | 'ack';
  agent: string;
  from?: string;
  to?: string;
  payload?: string;
  role?: string;
}

interface AgentInfo {
  id: string;
  role: string;
  sent: number;
  received: number;
  firstTick: number;
  lastTick: number;
}

type SortKey = keyof AgentInfo;
type SortDir = 'asc' | 'desc';
type Tab = 'map' | 'timeline' | 'stats';

/* ------------------------------------------------------------------ */
/*  Demo data                                                          */
/* ------------------------------------------------------------------ */

const ROLES: Record<string, string> = {
  buyer:      '#C45A3C',  // rust
  seller:     '#221F1A',  // ink
  auctioneer: '#5C6E5A',  // sage
  observer:   '#8C8576',  // ink-300
  broker:     '#B58432',  // amber
};

function generateDemoTrace(): TraceEvent[] {
  const agents: { id: string; role: string }[] = [
    { id: 'buyer-0', role: 'buyer' },
    { id: 'buyer-1', role: 'buyer' },
    { id: 'buyer-2', role: 'buyer' },
    { id: 'seller-0', role: 'seller' },
    { id: 'seller-1', role: 'seller' },
    { id: 'auctioneer-0', role: 'auctioneer' },
    { id: 'broker-0', role: 'broker' },
    { id: 'observer-0', role: 'observer' },
  ];

  const events: TraceEvent[] = [];
  for (const a of agents) {
    events.push({ tick: 0, kind: 'start', agent: a.id, role: a.role });
  }

  let seed = 42;
  const rand = () => {
    seed = (seed * 16807 + 0) % 2147483647;
    return (seed - 1) / 2147483646;
  };

  const products = ['laptop', 'phone', 'tablet', 'monitor', 'keyboard'];

  for (let tick = 1; tick <= 18; tick++) {
    for (let b = 0; b < 3; b++) {
      if (rand() > 0.35) {
        const product = products[Math.floor(rand() * products.length)];
        const price = Math.floor(rand() * 200 + 20);
        events.push({
          tick, kind: 'bid', agent: `buyer-${b}`,
          from: `buyer-${b}`, to: 'auctioneer-0',
          payload: `bid:${product}:${price}`,
        });
      }
    }
    for (let s = 0; s < 2; s++) {
      if (rand() > 0.4) {
        const product = products[Math.floor(rand() * products.length)];
        const price = Math.floor(rand() * 180 + 30);
        events.push({
          tick, kind: 'ask', agent: `seller-${s}`,
          from: `seller-${s}`, to: 'auctioneer-0',
          payload: `ask:${product}:${price}`,
        });
      }
    }
    if (rand() > 0.5) {
      const buyerIdx = Math.floor(rand() * 3);
      const sellerIdx = Math.floor(rand() * 2);
      events.push({
        tick, kind: 'ack', agent: 'auctioneer-0',
        from: 'auctioneer-0', to: `buyer-${buyerIdx}`,
        payload: 'match:confirmed',
      });
      events.push({
        tick, kind: 'ack', agent: 'auctioneer-0',
        from: 'auctioneer-0', to: `seller-${sellerIdx}`,
        payload: 'match:confirmed',
      });
    }
    if (rand() > 0.6) {
      const from = `buyer-${Math.floor(rand() * 3)}`;
      const to = `seller-${Math.floor(rand() * 2)}`;
      events.push({
        tick, kind: 'send', agent: 'broker-0',
        from: 'broker-0', to, payload: `relay:${from}`,
      });
    }
    if (tick % 4 === 0) {
      events.push({
        tick, kind: 'send', agent: 'observer-0',
        from: 'observer-0', to: 'auctioneer-0',
        payload: 'heartbeat',
      });
    }
  }
  for (const a of agents) {
    events.push({ tick: 20, kind: 'stop', agent: a.id, role: a.role });
  }
  return events;
}

/* ------------------------------------------------------------------ */
/*  Derived data                                                       */
/* ------------------------------------------------------------------ */

function deriveAgents(events: TraceEvent[]): AgentInfo[] {
  const map = new Map<string, AgentInfo>();
  const ensure = (id: string, role?: string) => {
    if (!map.has(id)) {
      map.set(id, {
        id,
        role: role ?? id.replace(/-\d+$/, ''),
        sent: 0, received: 0,
        firstTick: Infinity, lastTick: -Infinity,
      });
    }
    return map.get(id)!;
  };
  for (const e of events) {
    const a = ensure(e.agent, e.role);
    a.firstTick = Math.min(a.firstTick, e.tick);
    a.lastTick = Math.max(a.lastTick, e.tick);
    if (e.from) {
      const s = ensure(e.from);
      s.sent++; s.firstTick = Math.min(s.firstTick, e.tick);
      s.lastTick = Math.max(s.lastTick, e.tick);
    }
    if (e.to) {
      const r = ensure(e.to);
      r.received++; r.firstTick = Math.min(r.firstTick, e.tick);
      r.lastTick = Math.max(r.lastTick, e.tick);
    }
  }
  return Array.from(map.values());
}

interface EdgeInfo {
  source: string;
  target: string;
  count: number;
}

function deriveEdges(events: TraceEvent[]): EdgeInfo[] {
  const map = new Map<string, number>();
  for (const e of events) {
    if (e.from && e.to) {
      const key = [e.from, e.to].sort().join('::');
      map.set(key, (map.get(key) ?? 0) + 1);
    }
  }
  return Array.from(map.entries()).map(([key, count]) => {
    const [source, target] = key.split('::');
    return { source, target, count };
  });
}

function roleColor(role: string): string {
  return ROLES[role] ?? '#6B6557';
}

/* ------------------------------------------------------------------ */
/*  Communication Map                                                  */
/* ------------------------------------------------------------------ */

function CommunicationMap({ events }: { events: TraceEvent[] }) {
  const [hovered, setHovered] = useState<string | null>(null);
  const agents = useMemo(() => deriveAgents(events), [events]);
  const edges = useMemo(() => deriveEdges(events), [events]);

  const maxEdgeCount = useMemo(
    () => Math.max(...edges.map((e) => e.count), 1),
    [edges],
  );

  const cx = 300;
  const cy = 300;
  const radius = 220;
  const positions = useMemo(() => {
    const pos = new Map<string, { x: number; y: number }>();
    agents.forEach((a, i) => {
      const angle = (2 * Math.PI * i) / agents.length - Math.PI / 2;
      pos.set(a.id, {
        x: cx + radius * Math.cos(angle),
        y: cy + radius * Math.sin(angle),
      });
    });
    return pos;
  }, [agents]);

  const connectedTo = useMemo(() => {
    if (!hovered) return new Set<string>();
    const set = new Set<string>();
    for (const e of edges) {
      if (e.source === hovered) set.add(e.target);
      if (e.target === hovered) set.add(e.source);
    }
    return set;
  }, [hovered, edges]);

  const uniqueRoles = useMemo(() => {
    const set = new Set<string>();
    agents.forEach((a) => set.add(a.role));
    return Array.from(set);
  }, [agents]);

  return (
    <div className="rounded-2xl border border-cream-400/70 bg-cream-50 p-8">
      <svg
        viewBox="0 0 600 600"
        className="w-full max-w-[700px] mx-auto"
        style={{ overflow: 'visible' }}
      >
        <defs>
          <filter id="vis-glow">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <filter id="vis-shadow">
            <feDropShadow dx="0" dy="1" stdDeviation="2" floodOpacity="0.15" />
          </filter>
        </defs>

        {/* Edges */}
        {edges.map((edge) => {
          const p1 = positions.get(edge.source);
          const p2 = positions.get(edge.target);
          if (!p1 || !p2) return null;
          const isHighlighted =
            !hovered || edge.source === hovered || edge.target === hovered;
          const thickness = 1 + (edge.count / maxEdgeCount) * 5;
          return (
            <line
              key={`${edge.source}-${edge.target}`}
              x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y}
              stroke={isHighlighted ? '#C45A3C' : '#DDD7C5'}
              strokeWidth={isHighlighted ? thickness : thickness * 0.5}
              strokeOpacity={isHighlighted ? 0.55 : 0.25}
              strokeLinecap="round"
              style={{ transition: 'all 0.35s cubic-bezier(0.4, 0, 0.2, 1)' }}
            />
          );
        })}

        {/* Edge labels on hover */}
        {hovered &&
          edges
            .filter((e) => e.source === hovered || e.target === hovered)
            .map((edge) => {
              const p1 = positions.get(edge.source);
              const p2 = positions.get(edge.target);
              if (!p1 || !p2) return null;
              const mx = (p1.x + p2.x) / 2;
              const my = (p1.y + p2.y) / 2;
              return (
                <g key={`label-${edge.source}-${edge.target}`}>
                  <circle cx={mx} cy={my} r={12} fill="#F7F5EF" filter="url(#vis-shadow)" />
                  <text
                    x={mx} y={my} textAnchor="middle" dominantBaseline="central"
                    fontSize={10} fontWeight={600} fill="#141312"
                  >
                    {edge.count}
                  </text>
                </g>
              );
            })}

        {/* Nodes */}
        {agents.map((agent) => {
          const pos = positions.get(agent.id);
          if (!pos) return null;
          const isActive =
            !hovered || hovered === agent.id || connectedTo.has(agent.id);
          const nodeRadius = hovered === agent.id ? 24 : 18;
          const color = roleColor(agent.role);
          return (
            <g
              key={agent.id}
              onMouseEnter={() => setHovered(agent.id)}
              onMouseLeave={() => setHovered(null)}
              style={{
                cursor: 'pointer',
                transition: 'opacity 0.35s ease',
                opacity: isActive ? 1 : 0.2,
              }}
            >
              {hovered === agent.id && (
                <circle
                  cx={pos.x} cy={pos.y} r={nodeRadius + 6}
                  fill="none" stroke={color}
                  strokeWidth={2} strokeOpacity={0.3}
                  className="animate-pulse-dot"
                />
              )}
              <circle
                cx={pos.x} cy={pos.y} r={nodeRadius}
                fill={color}
                filter={hovered === agent.id ? 'url(#vis-glow)' : undefined}
                style={{ transition: 'r 0.35s cubic-bezier(0.4, 0, 0.2, 1)' }}
              />
              <text
                x={pos.x} y={pos.y}
                textAnchor="middle" dominantBaseline="central"
                fontSize={9} fontWeight={700} fill="#F7F5EF"
                style={{ pointerEvents: 'none' }}
              >
                {agent.id.split('-')[0][0].toUpperCase()}
                {agent.id.split('-')[1]}
              </text>
              <text
                x={pos.x} y={pos.y + nodeRadius + 16}
                textAnchor="middle"
                fontSize={11} fontWeight={500} fill="#353129"
                style={{
                  transition: 'opacity 0.35s ease',
                  opacity: isActive ? 1 : 0.3,
                  pointerEvents: 'none',
                }}
              >
                {agent.id}
              </text>
            </g>
          );
        })}
      </svg>

      <div className="flex flex-wrap items-center justify-center gap-5 pt-4">
        {uniqueRoles.map((role) => (
          <div key={role} className="flex items-center gap-2">
            <span
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: roleColor(role) }}
            />
            <span className="text-[0.85rem] text-ink-500 capitalize">{role}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Timeline                                                           */
/* ------------------------------------------------------------------ */

const KIND_COLORS: Record<string, string> = {
  start: '#6B8559', stop:  '#6B6557',
  send:  '#C45A3C', recv:  '#B58432',
  bid:   '#C45A3C', ask:   '#5C6E5A',
  ack:   '#221F1A',
};

function Timeline({ events }: { events: TraceEvent[] }) {
  const maxTick = useMemo(
    () => Math.max(...events.map((e) => e.tick), 1),
    [events],
  );
  const [selectedTick, setSelectedTick] = useState<number | null>(null);

  const filteredEvents = useMemo(
    () =>
      selectedTick !== null
        ? events.filter((e) => e.tick === selectedTick)
        : events,
    [events, selectedTick],
  );

  const ticks = useMemo(() => {
    const set = new Set(events.map((e) => e.tick));
    return Array.from(set).sort((a, b) => a - b);
  }, [events]);

  const tickGroups = useMemo(() => {
    const map = new Map<number, TraceEvent[]>();
    for (const e of events) {
      if (!map.has(e.tick)) map.set(e.tick, []);
      map.get(e.tick)!.push(e);
    }
    return map;
  }, [events]);

  const svgWidth = 900;
  const svgHeight = 160;
  const paddingX = 60;
  const baseY = 90;

  const xScale = useCallback(
    (tick: number) => paddingX + ((svgWidth - paddingX * 2) * tick) / maxTick,
    [maxTick],
  );

  return (
    <div className="space-y-6">
      <div className="overflow-x-auto rounded-2xl border border-cream-400/70 bg-cream-50 p-6">
        <svg
          viewBox={`0 0 ${svgWidth} ${svgHeight}`}
          className="w-full min-w-[600px]"
          style={{ overflow: 'visible' }}
        >
          <line
            x1={paddingX} y1={baseY}
            x2={svgWidth - paddingX} y2={baseY}
            stroke="#DDD7C5" strokeWidth={1.5}
          />
          {ticks.map((t) => (
            <g key={`tick-${t}`}>
              <line
                x1={xScale(t)} y1={baseY - 4}
                x2={xScale(t)} y2={baseY + 4}
                stroke="#B5AE9F" strokeWidth={1}
              />
              <text
                x={xScale(t)} y={baseY + 20}
                textAnchor="middle"
                fontSize={10} fill="#6B6557"
              >
                {t}
              </text>
            </g>
          ))}
          <text
            x={svgWidth / 2} y={baseY + 42}
            textAnchor="middle" fontSize={11} fill="#8C8576"
            fontFamily="var(--font-mono)"
            letterSpacing={1.5}
          >
            TICK
          </text>
          {ticks.map((t) => {
            const group = tickGroups.get(t) ?? [];
            return group.map((ev, i) => {
              const color = KIND_COLORS[ev.kind] ?? '#6B6557';
              const yOffset = -(i * 10 + 12);
              const isSelected = selectedTick === null || selectedTick === t;
              return (
                <circle
                  key={`dot-${t}-${i}`}
                  cx={xScale(t)} cy={baseY + yOffset}
                  r={isSelected ? 5 : 3}
                  fill={color}
                  fillOpacity={isSelected ? 0.9 : 0.2}
                  stroke={isSelected ? color : 'none'}
                  strokeWidth={1.5} strokeOpacity={0.3}
                  style={{ cursor: 'pointer', transition: 'all 0.25s ease' }}
                  onClick={() =>
                    setSelectedTick(selectedTick === t ? null : t)
                  }
                />
              );
            });
          })}
        </svg>
      </div>

      {/* Kind legend */}
      <div className="flex flex-wrap items-center gap-5">
        {Object.entries(KIND_COLORS).map(([kind, color]) => (
          <div key={kind} className="flex items-center gap-2">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: color }}
            />
            <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-400">
              {kind}
            </span>
          </div>
        ))}
      </div>

      {/* Event log */}
      <div className="rounded-2xl border border-cream-400/70 bg-cream-50">
        <div className="flex items-center justify-between border-b border-cream-400/70 px-6 py-4">
          <h3 className="font-mono text-[10px] uppercase tracking-[0.22em] text-ink-400">
            Event log
            {selectedTick !== null && (
              <span className="ml-3 text-ink-300">tick {selectedTick}</span>
            )}
          </h3>
          {selectedTick !== null && (
            <button
              onClick={() => setSelectedTick(null)}
              className="text-[0.8rem] text-rust hover:text-ink-900 transition-colors"
            >
              Show all
            </button>
          )}
        </div>
        <div className="max-h-80 overflow-y-auto divide-y divide-cream-400/40">
          {filteredEvents.map((ev, i) => (
            <div
              key={i}
              className="flex items-center gap-4 px-6 py-2.5 hover:bg-cream-200/60 transition-colors"
            >
              <span className="w-10 shrink-0 text-right font-mono text-[11px] text-ink-300 tabular-nums">
                {ev.tick}
              </span>
              <span
                className="inline-flex h-5 items-center rounded-sm px-2 text-[10px] font-semibold uppercase tracking-wider text-cream-50"
                style={{
                  backgroundColor: KIND_COLORS[ev.kind] ?? '#6B6557',
                }}
              >
                {ev.kind}
              </span>
              <span className="text-[0.88rem] text-ink-900 font-medium">
                {ev.agent}
              </span>
              {ev.from && ev.to && (
                <span className="font-mono text-[11px] text-ink-300">
                  {ev.from} &rarr; {ev.to}
                </span>
              )}
              {ev.payload && (
                <span className="ml-auto truncate text-[11px] font-mono text-ink-400 max-w-[220px]">
                  {ev.payload}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Agent stats                                                        */
/* ------------------------------------------------------------------ */

function AgentStats({ events }: { events: TraceEvent[] }) {
  const agents = useMemo(() => deriveAgents(events), [events]);
  const [sortKey, setSortKey] = useState<SortKey>('id');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const sorted = useMemo(() => {
    const copy = [...agents];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortDir === 'asc' ? av - bv : bv - av;
      }
      return sortDir === 'asc'
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av));
    });
    return copy;
  }, [agents, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  const columns: { key: SortKey; label: string }[] = [
    { key: 'id', label: 'Agent' },
    { key: 'role', label: 'Role' },
    { key: 'sent', label: 'Sent' },
    { key: 'received', label: 'Received' },
    { key: 'firstTick', label: 'First active' },
    { key: 'lastTick', label: 'Last active' },
  ];

  return (
    <div className="rounded-2xl border border-cream-400/70 bg-cream-50 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-cream-400/70 bg-cream-200">
              {columns.map((col) => (
                <th
                  key={col.key}
                  onClick={() => toggleSort(col.key)}
                  className="cursor-pointer select-none px-5 py-4 font-mono text-[10px] uppercase tracking-[0.22em] text-ink-400 hover:text-ink-900 transition-colors"
                >
                  {col.label}
                  {sortKey === col.key && (
                    <span className="ml-1 text-rust">
                      {sortDir === 'asc' ? '↑' : '↓'}
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-cream-400/40">
            {sorted.map((agent) => (
              <tr key={agent.id} className="hover:bg-cream-200/60 transition-colors">
                <td className="px-5 py-3.5 text-[0.92rem] font-medium text-ink-900">
                  <span className="flex items-center gap-2.5">
                    <span
                      className="inline-block h-2 w-2 rounded-full"
                      style={{ backgroundColor: roleColor(agent.role) }}
                    />
                    {agent.id}
                  </span>
                </td>
                <td className="px-5 py-3.5 text-[0.88rem] text-ink-500 capitalize">
                  {agent.role}
                </td>
                <td className="px-5 py-3.5 text-[0.9rem] font-mono text-ink-700 tabular-nums">
                  {agent.sent}
                </td>
                <td className="px-5 py-3.5 text-[0.9rem] font-mono text-ink-700 tabular-nums">
                  {agent.received}
                </td>
                <td className="px-5 py-3.5 text-[0.88rem] font-mono text-ink-400 tabular-nums">
                  {agent.firstTick === Infinity ? '—' : agent.firstTick}
                </td>
                <td className="px-5 py-3.5 text-[0.88rem] font-mono text-ink-400 tabular-nums">
                  {agent.lastTick === -Infinity ? '—' : agent.lastTick}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  File upload                                                        */
/* ------------------------------------------------------------------ */

function parseTraceFile(text: string): TraceEvent[] {
  const lines = text.trim().split('\n');
  const events: TraceEvent[] = [];
  for (const line of lines) {
    try {
      const parsed = JSON.parse(line);
      if (parsed && typeof parsed.tick === 'number' && parsed.kind) {
        events.push(parsed as TraceEvent);
      }
    } catch {
      /* skip */
    }
  }
  return events;
}

/* ------------------------------------------------------------------ */
/*  Page                                                               */
/* ------------------------------------------------------------------ */

export default function VisualizerPage() {
  const [events, setEvents] = useState<TraceEvent[] | null>(null);
  const [tab, setTab] = useState<Tab>('map');
  const [dragOver, setDragOver] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback((file: File) => {
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target?.result as string;
      const parsed = parseTraceFile(text);
      if (parsed.length > 0) {
        setEvents(parsed);
        setTab('map');
      }
    };
    reader.readAsText(file);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const loadDemo = useCallback(() => {
    setEvents(generateDemoTrace());
    setFileName(null);
    setTab('map');
  }, []);

  const tabs: { id: Tab; label: string }[] = [
    { id: 'map', label: 'Communication map' },
    { id: 'timeline', label: 'Timeline' },
    { id: 'stats', label: 'Agent stats' },
  ];

  return (
    <div className="bg-cream-100">
      {/* Header */}
      <section className="paper-texture border-b border-cream-400/70">
        <div className="mx-auto max-w-[1240px] px-6 sm:px-10 pt-20 pb-16">
          <div className="flex items-center gap-3 mb-10 animate-fade-in">
            <span className="inline-flex h-1.5 w-1.5 rounded-full bg-rust" />
            <span className="eyebrow">Visualizer &middot; JSONL traces</span>
          </div>

          <div className="grid gap-12 lg:grid-cols-[1.4fr_1fr] lg:items-end">
            <h1 className="font-display animate-fade-in stagger-1 text-[clamp(2.6rem,6vw,5rem)] leading-[1.02] tracking-tight text-ink-900">
              Replay a<br />
              <span className="italic text-ink-700">simulation</span>,<br />
              tick by tick.
            </h1>
            <p className="animate-fade-in stagger-2 text-[1.1rem] leading-[1.6] text-ink-500 max-w-md">
              Drop a NEST trace file to inspect the communication map, scrub
              through the timeline, and read every event. Or load the demo
              to see how it works without leaving the page.
            </p>
          </div>
        </div>
      </section>

      <div className="mx-auto max-w-[1240px] px-6 sm:px-10 py-12">
        {/* Upload row */}
        <div className="flex flex-col gap-4 sm:flex-row sm:items-stretch animate-fade-in stagger-1">
          <button
            onClick={loadDemo}
            className="flex items-center justify-center gap-3 rounded-2xl border border-ink-900 bg-ink-900 text-cream-50 px-8 py-6 text-[0.92rem] font-medium transition-colors hover:bg-ink-700 active:scale-[0.99]"
          >
            <span>Load demo trace</span>
          </button>

          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`flex flex-1 cursor-pointer items-center justify-center rounded-2xl border-2 border-dashed px-8 py-6 text-[0.92rem] transition-all ${
              dragOver
                ? 'border-rust bg-rust-bg/40 text-rust'
                : 'border-cream-400 bg-cream-50 text-ink-400 hover:border-ink-300 hover:text-ink-700'
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".jsonl,.json,.ndjson"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleFile(file);
              }}
            />
            <span>
              {fileName
                ? `Loaded: ${fileName}`
                : 'Drop a .jsonl trace file, or click to browse'}
            </span>
          </div>
        </div>

        {/* Content */}
        {events && events.length > 0 && (
          <div className="mt-12 animate-fade-in stagger-2">
            {/* Summary */}
            <div className="mb-10 grid grid-cols-2 md:grid-cols-4 gap-8 border-t border-cream-400/70 pt-8">
              {[
                { label: 'Events', value: events.length },
                { label: 'Agents', value: deriveAgents(events).length },
                {
                  label: 'Ticks',
                  value: Math.max(...events.map((e) => e.tick)) + 1,
                },
                {
                  label: 'Connections',
                  value: deriveEdges(events).length,
                },
              ].map((stat) => (
                <div key={stat.label}>
                  <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-ink-300">
                    {stat.label}
                  </p>
                  <p className="mt-2 font-display text-[2.2rem] leading-none text-ink-900 tabular-nums">
                    {stat.value}
                  </p>
                </div>
              ))}
            </div>

            {/* Tabs */}
            <div className="flex gap-1 border-b border-cream-400/70 mb-8">
              {tabs.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`relative px-5 py-3 text-[0.92rem] font-medium transition-colors ${
                    tab === t.id
                      ? 'text-ink-900'
                      : 'text-ink-400 hover:text-ink-900'
                  }`}
                >
                  {t.label}
                  {tab === t.id && (
                    <span className="absolute left-3 right-3 -bottom-px h-[2px] bg-ink-900" />
                  )}
                </button>
              ))}
            </div>

            <div>
              {tab === 'map' && <CommunicationMap events={events} />}
              {tab === 'timeline' && <Timeline events={events} />}
              {tab === 'stats' && <AgentStats events={events} />}
            </div>
          </div>
        )}

        {/* Empty state */}
        {!events && (
          <div className="mt-16 rounded-2xl border border-cream-400/70 bg-cream-50 p-12 text-center animate-fade-in stagger-3">
            <p className="font-display text-[1.6rem] italic text-ink-400 leading-tight">
              Nothing loaded yet.
            </p>
            <p className="mt-3 text-[0.95rem] text-ink-400">
              Try the demo trace or drop in a <span className="font-mono text-ink-700">.jsonl</span> file
              from <span className="font-mono text-ink-700">nest run</span>.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
