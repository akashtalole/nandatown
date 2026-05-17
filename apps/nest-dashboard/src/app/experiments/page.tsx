'use client';

import { useState } from 'react';
import Link from 'next/link';
import { experiments, scenarioColors } from '@/lib/demo-data';

const scenarioFilters = [
  'All',
  'Marketplace',
  'Auction',
  'Voting',
  'Consensus',
  'Supply Chain',
  'Reputation',
] as const;

function scenarioFilterKey(label: string): string {
  if (label === 'All') return 'all';
  if (label === 'Supply Chain') return 'supply_chain';
  return label.toLowerCase();
}

function formatScenarioLabel(scenario: string): string {
  return scenario
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

const yamlSnippets: Record<string, string> = {
  marketplace: `scenario: marketplace
agents:
  buyers: 50
  sellers: 50
rounds: 10
protocol: negotiation
metrics:
  - delivery_rate
  - deal_rate
  - mean_latency
  - throughput`,
  auction: `scenario: auction
agents:
  auctioneer: 1
  bidders: 49
rounds: 5
protocol: sealed_bid
reserve_price: 100
metrics:
  - delivery_rate
  - deal_rate
  - mean_rounds_to_deal`,
  voting: `scenario: voting
agents:
  proposer: 1
  voters: 20
  coordinator: 1
rounds: 3
protocol: majority_vote
quorum: 0.5
metrics:
  - delivery_rate
  - duration`,
  consensus: `scenario: consensus
agents:
  leader: 1
  followers: 6
quorum: 0.667
rounds: 10
metrics:
  - delivery_rate
  - message_count
  - agent_count`,
  supply_chain: `scenario: supply_chain
agents:
  supplier: 1
  manufacturer: 1
  distributor: 1
  retailer: 1
hops: 4
protocol: pipeline
metrics:
  - mean_latency
  - delivery_rate`,
  reputation: `scenario: reputation
agents:
  honest_traders: 6
  malicious_traders: 2
  observer: 1
  coordinator: 1
malicious_ratio: 0.2
protocol: reputation_tracking
metrics:
  - delivery_rate
  - unique_pairs`,
};

export default function ExperimentsPage() {
  const [activeFilter, setActiveFilter] = useState('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const filtered =
    activeFilter === 'all'
      ? experiments
      : experiments.filter((e) => e.scenario === activeFilter);

  return (
    <div className="min-h-screen bg-warm-50">
      {/* ---- Header ---- */}
      <section className="border-b border-warm-200 bg-white">
        <div className="mx-auto max-w-7xl px-6 py-16 animate-fade-in">
          <h1 className="text-4xl font-bold tracking-tight text-warm-900 sm:text-5xl">
            Experiments
          </h1>
          <p className="mt-4 max-w-2xl text-lg leading-relaxed text-warm-500">
            Explore pre-built scenarios. See how agents interact without writing
            a single line of code.
          </p>
        </div>
      </section>

      {/* ---- Scenario Filters ---- */}
      <section className="border-b border-warm-200 bg-white">
        <div className="mx-auto max-w-7xl px-6 py-4">
          <div className="flex flex-wrap gap-2">
            {scenarioFilters.map((label) => {
              const key = scenarioFilterKey(label);
              const isActive = activeFilter === key;
              return (
                <button
                  key={key}
                  onClick={() => setActiveFilter(key)}
                  className={`rounded-full px-4 py-1.5 text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-warm-900 text-white'
                      : 'bg-warm-100 text-warm-600 hover:bg-warm-200 hover:text-warm-800'
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>
      </section>

      {/* ---- Experiment Cards ---- */}
      <section className="mx-auto max-w-7xl px-6 py-12">
        <div className="grid gap-6 md:grid-cols-2">
          {filtered.map((exp, idx) => {
            const color = scenarioColors[exp.scenario] ?? '#8B0000';
            const isExpanded = expandedId === exp.id;
            const staggerClass = `stagger-${(idx % 6) + 1}`;

            return (
              <div
                key={exp.id}
                className={`animate-fade-in ${staggerClass} rounded-2xl border border-warm-200 bg-white shadow-sm transition-shadow hover:shadow-md`}
              >
                {/* Card Body */}
                <div className="p-6">
                  {/* Scenario Badge */}
                  <div className="flex items-center gap-2 mb-3">
                    <span
                      className="inline-block h-2.5 w-2.5 rounded-full"
                      style={{ backgroundColor: color }}
                    />
                    <span
                      className="text-xs font-semibold uppercase tracking-wider"
                      style={{ color }}
                    >
                      {formatScenarioLabel(exp.scenario)}
                    </span>
                  </div>

                  {/* Name */}
                  <h2 className="text-xl font-bold text-warm-900 leading-tight">
                    {exp.name}
                  </h2>

                  {/* Description */}
                  <p className="mt-2 text-sm leading-relaxed text-warm-500 line-clamp-3">
                    {exp.description}
                  </p>

                  {/* Stats Row */}
                  <div className="mt-4 flex flex-wrap gap-4 text-sm text-warm-500">
                    <div className="flex items-center gap-1.5">
                      <svg
                        className="h-4 w-4 text-warm-400"
                        fill="none"
                        viewBox="0 0 24 24"
                        strokeWidth={1.5}
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128H9m6 0a5.972 5.972 0 0 0-.786-3.07M9 19.128A5.972 5.972 0 0 1 8.214 16.058M9 19.128v-.003c0-1.113.285-2.16.786-3.07m0 0A9.004 9.004 0 0 1 12 12.75a9.004 9.004 0 0 1 2.214 3.258M3.75 19.128a4.125 4.125 0 0 1 7.533-2.493"
                        />
                      </svg>
                      <span>
                        <span className="font-medium text-warm-700">
                          {exp.agents}
                        </span>{' '}
                        agents
                      </span>
                    </div>
                    {exp.metrics && (
                      <div className="flex items-center gap-1.5">
                        <svg
                          className="h-4 w-4 text-warm-400"
                          fill="none"
                          viewBox="0 0 24 24"
                          strokeWidth={1.5}
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.076-4.076a1.526 1.526 0 0 1 1.037-.443 48.14 48.14 0 0 0 5.637-.5c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z"
                          />
                        </svg>
                        <span>
                          <span className="font-medium text-warm-700">
                            {exp.metrics.messageCount.toLocaleString()}
                          </span>{' '}
                          messages
                        </span>
                      </div>
                    )}
                    {exp.duration && (
                      <div className="flex items-center gap-1.5">
                        <svg
                          className="h-4 w-4 text-warm-400"
                          fill="none"
                          viewBox="0 0 24 24"
                          strokeWidth={1.5}
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
                          />
                        </svg>
                        <span>
                          <span className="font-medium text-warm-700">
                            {exp.duration}
                          </span>{' '}
                          duration
                        </span>
                      </div>
                    )}
                  </div>

                  {/* Metrics Bar: Delivery Rate */}
                  {exp.metrics && (
                    <div className="mt-5">
                      <div className="flex items-center justify-between text-sm mb-1.5">
                        <span className="font-medium text-warm-600">
                          Delivery Rate
                        </span>
                        <span className="font-semibold text-warm-900">
                          {exp.metrics.deliveryRate}%
                        </span>
                      </div>
                      <div className="h-2 w-full rounded-full bg-warm-100">
                        <div
                          className="h-2 rounded-full transition-all duration-500"
                          style={{
                            width: `${exp.metrics.deliveryRate}%`,
                            backgroundColor: color,
                          }}
                        />
                      </div>
                    </div>
                  )}

                  {/* View Details Button */}
                  <button
                    onClick={() =>
                      setExpandedId(isExpanded ? null : exp.id)
                    }
                    className="mt-5 inline-flex items-center gap-1.5 rounded-lg bg-crimson px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-crimson-light"
                  >
                    {isExpanded ? 'Hide Details' : 'View Details'}
                    <svg
                      className={`h-4 w-4 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                      fill="none"
                      viewBox="0 0 24 24"
                      strokeWidth={2}
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="m19.5 8.25-7.5 7.5-7.5-7.5"
                      />
                    </svg>
                  </button>
                </div>

                {/* Expanded Detail Section */}
                {isExpanded && exp.metrics && (
                  <div className="border-t border-warm-200 bg-warm-50 p-6 rounded-b-2xl animate-fade-in">
                    {/* Full Metrics Grid */}
                    <h3 className="text-sm font-semibold uppercase tracking-wider text-warm-500 mb-4">
                      Full Metrics
                    </h3>
                    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                      <MetricCard
                        label="Delivery Rate"
                        value={`${exp.metrics.deliveryRate}%`}
                      />
                      {exp.metrics.dealRate !== null && (
                        <MetricCard
                          label="Deal Rate"
                          value={`${exp.metrics.dealRate}%`}
                        />
                      )}
                      <MetricCard
                        label="Mean Latency"
                        value={`${exp.metrics.meanLatency} ticks`}
                      />
                      <MetricCard
                        label="Messages"
                        value={exp.metrics.messageCount.toLocaleString()}
                      />
                      <MetricCard
                        label="Throughput"
                        value={`${exp.metrics.throughput} msg/tick`}
                      />
                    </div>

                    {/* Mini Bar Chart */}
                    <h3 className="mt-6 text-sm font-semibold uppercase tracking-wider text-warm-500 mb-3">
                      Metric Comparison
                    </h3>
                    <div className="space-y-3">
                      <HorizontalBar
                        label="Delivery Rate"
                        value={exp.metrics.deliveryRate}
                        max={100}
                        color={color}
                        suffix="%"
                      />
                      {exp.metrics.dealRate !== null && (
                        <HorizontalBar
                          label="Deal Rate"
                          value={exp.metrics.dealRate}
                          max={100}
                          color={color}
                          suffix="%"
                        />
                      )}
                      <HorizontalBar
                        label="Latency"
                        value={exp.metrics.meanLatency}
                        max={10}
                        color={color}
                        suffix=" ticks"
                      />
                      <HorizontalBar
                        label="Messages"
                        value={exp.metrics.messageCount}
                        max={2500}
                        color={color}
                      />
                      <HorizontalBar
                        label="Throughput"
                        value={exp.metrics.throughput}
                        max={50}
                        color={color}
                        suffix=" msg/tick"
                      />
                    </div>

                    {/* Open in Visualizer */}
                    <div className="mt-6">
                      <Link
                        href="/visualizer"
                        className="inline-flex items-center gap-1.5 text-sm font-medium text-crimson hover:text-crimson-light transition-colors"
                      >
                        Open in Visualizer
                        <svg
                          className="h-4 w-4"
                          fill="none"
                          viewBox="0 0 24 24"
                          strokeWidth={2}
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"
                          />
                        </svg>
                      </Link>
                    </div>

                    {/* YAML Config Snippet */}
                    <h3 className="mt-6 text-sm font-semibold uppercase tracking-wider text-warm-500 mb-3">
                      Scenario Config
                    </h3>
                    <div className="rounded-lg bg-warm-900 p-4 overflow-x-auto">
                      <pre className="text-sm leading-relaxed text-warm-200 font-mono">
                        <code>{yamlSnippets[exp.scenario] ?? '# No config available'}</code>
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {filtered.length === 0 && (
          <div className="mt-12 text-center animate-fade-in">
            <p className="text-warm-400 text-lg">
              No experiments found for this scenario.
            </p>
          </div>
        )}
      </section>

      {/* ---- How to Run Your Own ---- */}
      <section className="border-t border-warm-200 bg-white">
        <div className="mx-auto max-w-7xl px-6 py-16 animate-fade-in stagger-3">
          <h2 className="text-2xl font-bold tracking-tight text-warm-900">
            How to Run Your Own
          </h2>
          <p className="mt-3 text-warm-500 leading-relaxed max-w-xl">
            Install the NEST CLI and run any scenario with a single command.
            Define your own agents, protocols, and metrics in YAML.
          </p>

          <div className="mt-6 rounded-lg bg-warm-900 p-4 max-w-xl overflow-x-auto">
            <pre className="text-sm leading-relaxed text-warm-200 font-mono">
              <code>pip install "nest-core[plugins]" &amp;&amp; nest run scenarios/marketplace.yaml</code>
            </pre>
          </div>

          <div className="mt-6">
            <Link
              href="/docs"
              className="inline-flex items-center gap-2 rounded-lg border border-warm-200 px-5 py-2.5 text-sm font-medium text-warm-700 transition-colors hover:bg-warm-50"
            >
              Read the full guide
              <svg
                className="h-4 w-4"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={2}
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M17.25 8.25 21 12m0 0-3.75 3.75M21 12H3"
                />
              </svg>
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}

/* ---- Sub-components ---- */

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-white border border-warm-200 p-3">
      <p className="text-xs font-medium text-warm-400 uppercase tracking-wider">
        {label}
      </p>
      <p className="mt-1 text-lg font-bold text-warm-900">{value}</p>
    </div>
  );
}

function HorizontalBar({
  label,
  value,
  max,
  color,
  suffix = '',
}: {
  label: string;
  value: number;
  max: number;
  color: string;
  suffix?: string;
}) {
  const pct = Math.min((value / max) * 100, 100);

  return (
    <div className="flex items-center gap-3">
      <span className="w-24 shrink-0 text-xs font-medium text-warm-500 text-right">
        {label}
      </span>
      <div className="flex-1 h-3 rounded-full bg-warm-100 overflow-hidden">
        <div
          className="h-3 rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="w-16 shrink-0 text-xs font-semibold text-warm-700">
        {value.toLocaleString()}
        {suffix}
      </span>
    </div>
  );
}
