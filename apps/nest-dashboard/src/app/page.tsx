'use client';

import { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { liveAgentChat, experiments, scenarioColors } from '@/lib/demo-data';
import type { AgentMessage } from '@/lib/demo-data';
import { MiniMap } from '@/components/mini-map';

/* ------------------------------------------------------------------ */
/*  Helper: scenario color from agent name                            */
/* ------------------------------------------------------------------ */
function getScenarioFromAgent(name: string): string {
  if (name.startsWith('buyer') || name.startsWith('seller'))
    return 'marketplace';
  if (name.startsWith('auctioneer') || name.startsWith('bidder'))
    return 'auction';
  if (
    name.startsWith('proposer') ||
    name.startsWith('voter') ||
    name.startsWith('coordinator')
  )
    return 'voting';
  if (
    name.startsWith('supplier') ||
    name.startsWith('manufacturer') ||
    name.startsWith('distributor') ||
    name.startsWith('retailer')
  )
    return 'supply_chain';
  return 'marketplace';
}

function scenarioLabel(scenario: string): string {
  const labels: Record<string, string> = {
    marketplace: 'Marketplace',
    auction: 'Auction',
    voting: 'Voting',
    supply_chain: 'Supply Chain',
    consensus: 'Consensus',
    reputation: 'Reputation',
  };
  return labels[scenario] ?? scenario;
}

/* ------------------------------------------------------------------ */
/*  Data: 12 Protocol Layers                                          */
/* ------------------------------------------------------------------ */
const protocolLayers = [
  { name: 'Transport', description: 'How bytes move between agents' },
  { name: 'Communication', description: 'Message format and semantics' },
  { name: 'Identity', description: 'Agent verification and credentials' },
  { name: 'Registry', description: 'Agent discovery and lookup' },
  { name: 'Auth', description: 'Authentication and access control' },
  { name: 'Trust', description: 'Reputation and reliability scores' },
  { name: 'Payments', description: 'Value transfer between agents' },
  { name: 'Coordination', description: 'Group decision-making protocols' },
  { name: 'Negotiation', description: 'Bargaining and deal-making' },
  { name: 'Memory', description: 'Shared state and persistence' },
  { name: 'Privacy', description: 'Encryption and zero-knowledge proofs' },
  { name: 'Data Facts', description: 'Dataset exchange and validation' },
];

/* ------------------------------------------------------------------ */
/*  Hook: animated message feed                                       */
/* ------------------------------------------------------------------ */
function useAnimatedMessages(messages: AgentMessage[], intervalMs = 1800) {
  const [visible, setVisible] = useState<AgentMessage[]>([]);
  const indexRef = useRef(0);

  useEffect(() => {
    // Show first message immediately
    setVisible([messages[0]]);
    indexRef.current = 1;

    const id = setInterval(() => {
      setVisible((prev) => {
        const next = messages[indexRef.current % messages.length];
        indexRef.current += 1;
        // Keep a rolling window of the most recent 8 messages
        const updated = [...prev, next];
        return updated.length > 8 ? updated.slice(-8) : updated;
      });
    }, intervalMs);

    return () => clearInterval(id);
  }, [messages, intervalMs]);

  return visible;
}

/* ================================================================== */
/*  Page                                                              */
/* ================================================================== */
export default function Home() {
  const chatMessages = useAnimatedMessages(liveAgentChat, 2000);
  const featuredExperiments = experiments.slice(0, 3);

  return (
    <div className="bg-warm-50">
      {/* ---------------------------------------------------------- */}
      {/*  HERO                                                      */}
      {/* ---------------------------------------------------------- */}
      <section className="relative overflow-hidden">
        {/* Subtle grid background */}
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.03]"
          style={{
            backgroundImage:
              'linear-gradient(to right, #1C1917 1px, transparent 1px), linear-gradient(to bottom, #1C1917 1px, transparent 1px)',
            backgroundSize: '60px 60px',
          }}
        />

        <div className="relative mx-auto max-w-7xl px-6 pb-24 pt-20 md:pt-32 md:pb-32">
          <div className="grid gap-12 lg:grid-cols-[1fr_minmax(0,520px)] lg:items-center">
            <div className="max-w-3xl animate-fade-in">
              <p className="text-sm font-medium uppercase tracking-widest text-crimson">
                Project NANDA &middot; MIT Media Lab
              </p>
              <h1 className="mt-6 text-5xl font-bold leading-[1.1] tracking-tight text-warm-950 md:text-7xl">
                Test Agent Protocols
                <br />
                at Scale
              </h1>
              <p className="mt-6 max-w-xl text-lg leading-8 text-warm-500">
                An open sandbox where AI agents talk, trade, vote, and cooperate
                &mdash; so you can see what works before going live.
              </p>

              <div className="mt-10 flex flex-wrap gap-4">
                <Link
                  href="/experiments"
                  className="inline-flex items-center rounded-lg bg-crimson px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-crimson-light"
                >
                  Try an Experiment
                </Link>
                <Link
                  href="/leaderboard"
                  className="inline-flex items-center rounded-lg border border-warm-300 px-6 py-3 text-sm font-semibold text-warm-900 transition-colors hover:border-warm-400 hover:bg-warm-100"
                >
                  View Leaderboard
                </Link>
              </div>
            </div>

            <Link
              href="/agents"
              className="hidden lg:block animate-fade-in stagger-1 group"
              aria-label="Open the full agent network map"
            >
              <MiniMap width={560} height={320} />
              <div className="mt-3 flex items-center justify-between px-1">
                <span className="text-[11px] font-mono uppercase tracking-widest text-warm-500">
                  Agent network · live
                </span>
                <span className="text-xs text-warm-500 group-hover:text-warm-900 transition-colors">
                  Open map →
                </span>
              </div>
            </Link>
          </div>

          {/* Mini chat preview in the hero */}
          <div className="mt-16 animate-slide-up stagger-2">
            <div className="rounded-xl border border-warm-200 bg-white shadow-sm overflow-hidden max-w-2xl">
              <div className="flex items-center gap-2 border-b border-warm-100 px-4 py-3">
                <span className="h-3 w-3 rounded-full bg-warm-300" />
                <span className="h-3 w-3 rounded-full bg-warm-200" />
                <span className="h-3 w-3 rounded-full bg-warm-200" />
                <span className="ml-3 text-xs font-medium text-warm-400 font-mono">
                  agent-feed
                </span>
              </div>
              <div className="px-4 py-3 space-y-1.5 font-mono text-xs h-[180px] overflow-hidden">
                {chatMessages.slice(-5).map((msg, i) => {
                  const scenario = getScenarioFromAgent(msg.from);
                  const color = scenarioColors[scenario] ?? '#78716C';
                  return (
                    <div
                      key={`hero-${msg.tick}-${i}`}
                      className="flex gap-2 animate-fade-in"
                    >
                      <span className="text-warm-400 shrink-0 w-12 text-right">
                        t={msg.tick}
                      </span>
                      <span
                        className="font-semibold shrink-0"
                        style={{ color }}
                      >
                        {msg.from}
                      </span>
                      <span className="text-warm-300">&rarr;</span>
                      <span className="text-warm-600 shrink-0">
                        {msg.to}
                      </span>
                      <span className="text-warm-500 truncate">
                        {msg.content}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------- */}
      {/*  LIVE AGENT CHAT                                            */}
      {/* ---------------------------------------------------------- */}
      <section className="border-t border-warm-200 bg-white">
        <div className="mx-auto max-w-7xl px-6 py-24">
          <div className="animate-fade-in">
            <p className="text-sm font-medium uppercase tracking-widest text-crimson">
              Live Feed
            </p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight text-warm-950 md:text-4xl">
              Watch Agents Communicate
            </h2>
            <p className="mt-4 max-w-2xl text-warm-500">
              Messages between agents streaming in real time. Each color
              represents a different scenario type.
            </p>
          </div>

          <div className="mt-10 animate-slide-up stagger-2">
            <div className="rounded-xl border border-warm-200 bg-warm-950 shadow-lg overflow-hidden">
              {/* Terminal title bar */}
              <div className="flex items-center gap-2 px-4 py-3 bg-warm-900">
                <span className="h-3 w-3 rounded-full bg-red-500/80" />
                <span className="h-3 w-3 rounded-full bg-yellow-500/80" />
                <span className="h-3 w-3 rounded-full bg-green-500/80" />
                <span className="ml-3 text-xs text-warm-400 font-mono">
                  nest --watch agent-messages
                </span>
              </div>

              {/* Message feed */}
              <div className="px-5 py-4 space-y-2 font-mono text-sm min-h-[340px]">
                {chatMessages.map((msg, i) => {
                  const scenario = getScenarioFromAgent(msg.from);
                  const color = scenarioColors[scenario] ?? '#78716C';
                  return (
                    <div
                      key={`chat-${msg.tick}-${i}`}
                      className="flex items-start gap-3 animate-fade-in"
                    >
                      <span className="text-warm-600 shrink-0 w-14 text-right tabular-nums">
                        [{String(msg.tick).padStart(2, '0')}]
                      </span>
                      <span
                        className="shrink-0 rounded px-1.5 py-0.5 text-xs font-bold"
                        style={{
                          backgroundColor: color + '18',
                          color: color,
                        }}
                      >
                        {scenarioLabel(scenario)}
                      </span>
                      <span className="text-warm-300 shrink-0">
                        {msg.from}
                      </span>
                      <span className="text-warm-600">&rarr;</span>
                      <span className="text-warm-400 shrink-0">{msg.to}</span>
                      <span className="text-warm-100">{msg.content}</span>
                    </div>
                  );
                })}

                {/* Blinking cursor */}
                <div className="flex items-center gap-3 mt-1">
                  <span className="text-warm-600 w-14" />
                  <span className="inline-block h-4 w-2 bg-crimson animate-pulse-dot" />
                </div>
              </div>
            </div>
          </div>

          {/* Scenario legend */}
          <div className="mt-6 flex flex-wrap gap-4">
            {Object.entries(scenarioColors).map(([key, color]) => (
              <div key={key} className="flex items-center gap-2 text-sm">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: color }}
                />
                <span className="text-warm-500">{scenarioLabel(key)}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------- */}
      {/*  HOW IT WORKS                                               */}
      {/* ---------------------------------------------------------- */}
      <section className="border-t border-warm-200">
        <div className="mx-auto max-w-7xl px-6 py-24">
          <div className="animate-fade-in">
            <p className="text-sm font-medium uppercase tracking-widest text-crimson">
              How It Works
            </p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight text-warm-950 md:text-4xl">
              Three Steps to Insight
            </h2>
          </div>

          <div className="mt-12 grid gap-8 md:grid-cols-3">
            {[
              {
                step: '01',
                title: 'Define',
                description:
                  'Write a YAML scenario or pick a built-in template. Specify the agents, their roles, and the protocol layers to test.',
              },
              {
                step: '02',
                title: 'Run',
                description:
                  'NEST spins up N agents and runs the simulation. Each agent follows its protocol stack, exchanging messages in real time.',
              },
              {
                step: '03',
                title: 'Analyze',
                description:
                  'Explore traces, metrics, and the communication map. See exactly how agents behaved and where protocols broke down.',
              },
            ].map((card, i) => (
              <div
                key={card.step}
                className={`animate-slide-up stagger-${i + 1} group rounded-xl border border-warm-200 bg-white p-8 transition-shadow hover:shadow-md`}
              >
                <span className="text-sm font-mono font-bold text-crimson">
                  {card.step}
                </span>
                <h3 className="mt-4 text-xl font-bold text-warm-950">
                  {card.title}
                </h3>
                <p className="mt-3 text-sm leading-6 text-warm-500">
                  {card.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------- */}
      {/*  THE 12 LAYERS                                              */}
      {/* ---------------------------------------------------------- */}
      <section className="border-t border-warm-200 bg-white">
        <div className="mx-auto max-w-7xl px-6 py-24">
          <div className="animate-fade-in">
            <p className="text-sm font-medium uppercase tracking-widest text-crimson">
              Architecture
            </p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight text-warm-950 md:text-4xl">
              The 12 Protocol Layers
            </h2>
            <p className="mt-4 max-w-2xl text-warm-500">
              NEST treats every layer as a pluggable module. Swap
              implementations, compare behaviors, and find the stack that works
              for your use case.
            </p>
          </div>

          <div className="mt-12 grid gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
            {protocolLayers.map((layer, i) => (
              <div
                key={layer.name}
                className={`animate-slide-up stagger-${(i % 6) + 1} rounded-lg border border-warm-200 bg-warm-50 p-5 transition-colors hover:border-crimson/30 hover:bg-white`}
              >
                <div className="flex items-center gap-3">
                  <span className="flex h-8 w-8 items-center justify-center rounded-md bg-warm-900 text-xs font-bold text-white font-mono">
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  <h3 className="text-sm font-bold text-warm-900">
                    {layer.name}
                  </h3>
                </div>
                <p className="mt-3 text-xs leading-5 text-warm-500">
                  {layer.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------- */}
      {/*  FEATURED EXPERIMENTS                                       */}
      {/* ---------------------------------------------------------- */}
      <section className="border-t border-warm-200">
        <div className="mx-auto max-w-7xl px-6 py-24">
          <div className="animate-fade-in">
            <p className="text-sm font-medium uppercase tracking-widest text-crimson">
              Featured
            </p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight text-warm-950 md:text-4xl">
              Recent Experiments
            </h2>
          </div>

          <div className="mt-12 grid gap-8 md:grid-cols-3">
            {featuredExperiments.map((exp, i) => {
              const color =
                scenarioColors[exp.scenario] ?? '#78716C';
              return (
                <div
                  key={exp.id}
                  className={`animate-slide-up stagger-${i + 1} group rounded-xl border border-warm-200 bg-white p-6 transition-shadow hover:shadow-md`}
                >
                  <div className="flex items-center justify-between">
                    <span
                      className="rounded-full px-2.5 py-1 text-xs font-semibold"
                      style={{
                        backgroundColor: color + '14',
                        color: color,
                      }}
                    >
                      {scenarioLabel(exp.scenario)}
                    </span>
                    <span className="text-xs text-warm-400">
                      {exp.agents} agents
                    </span>
                  </div>

                  <h3 className="mt-4 text-lg font-bold text-warm-950">
                    {exp.name}
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-warm-500 line-clamp-2">
                    {exp.description}
                  </p>

                  {exp.metrics && (
                    <div className="mt-5 grid grid-cols-2 gap-4 border-t border-warm-100 pt-5">
                      <div>
                        <p className="text-xs text-warm-400">Delivery Rate</p>
                        <p className="mt-1 text-lg font-bold text-warm-900">
                          {exp.metrics.deliveryRate}%
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-warm-400">Latency</p>
                        <p className="mt-1 text-lg font-bold text-warm-900">
                          {exp.metrics.meanLatency} ticks
                        </p>
                      </div>
                    </div>
                  )}

                  <Link
                    href="/experiments"
                    className="mt-5 inline-flex items-center text-sm font-medium text-crimson transition-colors hover:text-crimson-light"
                  >
                    View Results
                    <span className="ml-1 transition-transform group-hover:translate-x-0.5">
                      &rarr;
                    </span>
                  </Link>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------- */}
      {/*  CTA BANNER                                                 */}
      {/* ---------------------------------------------------------- */}
      <section className="border-t border-warm-200 bg-warm-950">
        <div className="mx-auto max-w-7xl px-6 py-20 text-center">
          <h2 className="animate-fade-in text-3xl font-bold tracking-tight text-white md:text-4xl">
            Ready to test your protocol?
          </h2>
          <p className="animate-fade-in stagger-1 mx-auto mt-4 max-w-lg text-warm-400">
            Define a scenario, run it against real agent behavior, and get
            actionable metrics in seconds.
          </p>
          <div className="animate-slide-up stagger-2 mt-10 flex flex-wrap items-center justify-center gap-4">
            <Link
              href="/docs"
              className="inline-flex items-center rounded-lg bg-crimson px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-crimson-light"
            >
              Read the Docs
            </Link>
            <Link
              href="/experiments"
              className="inline-flex items-center rounded-lg border border-warm-700 px-6 py-3 text-sm font-semibold text-warm-200 transition-colors hover:border-warm-500 hover:text-white"
            >
              Try It Now
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
