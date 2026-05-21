"use client";

// A small, decorative version of the /agents world map.
//
// Intended for hero / sidebar contexts: no labels, no agent jitter dots,
// no hover interaction.  Just countries + cluster pulses + a couple of
// animated message lines drifting between random clusters.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  geoNaturalEarth1,
  geoPath,
  type GeoPermissibleObjects,
  type GeoProjection,
} from "d3-geo";
import { feature } from "topojson-client";
import type {
  Topology,
  GeometryCollection,
  GeometryObject,
} from "topojson-specification";
import type { Feature, FeatureCollection } from "geojson";
import { clusters, clusterLinks, type MessageLink } from "@/lib/agent-network";

interface World {
  countries: FeatureCollection;
  projection: GeoProjection;
  path: (g: GeoPermissibleObjects) => string;
}

interface ActiveMessage {
  id: number;
  link: MessageLink;
  bornAt: number;
}

export interface MiniMapProps {
  /** Outer SVG viewBox width.  Default 560. */
  width?: number;
  /** Outer SVG viewBox height. Default 320. */
  height?: number;
  /** Tailwind classes for the wrapping <div>. */
  className?: string;
  /** Show the LIVE chip in the top-left corner. */
  showChip?: boolean;
  /** Override the projection scale (defaults are tuned for the size). */
  scale?: number;
}

export function MiniMap({
  width = 560,
  height = 320,
  className = "",
  showChip = true,
  scale,
}: MiniMapProps) {
  const [world, setWorld] = useState<World | null>(null);
  const [messages, setMessages] = useState<ActiveMessage[]>([]);
  const idRef = useRef(0);

  // Load + project once we have a size.
  useEffect(() => {
    let cancelled = false;
    fetch("/world-110m.json")
      .then((r) => r.json() as Promise<Topology>)
      .then((topo) => {
        if (cancelled) return;
        const countries = feature(
          topo,
          topo.objects.countries as GeometryCollection<GeometryObject>,
        ) as unknown as FeatureCollection;
        // The mini map crops Antarctica a little by translating downward.
        const projection = geoNaturalEarth1()
          .scale(scale ?? Math.min(width, height * 1.7) / 5.6)
          .translate([width / 2, height / 2 + 8]);
        const path = geoPath(projection) as unknown as (
          g: GeoPermissibleObjects,
        ) => string;
        setWorld({ countries, projection, path });
      })
      .catch(() => {
        /* leave map unrendered */
      });
    return () => {
      cancelled = true;
    };
  }, [width, height, scale]);

  // Spawn message links periodically.
  useEffect(() => {
    const intervalMs = 850;
    const lifetimeMs = 2600;
    const tick = setInterval(() => {
      const now = performance.now();
      idRef.current += 1;
      const link =
        clusterLinks[Math.floor(Math.random() * clusterLinks.length)];
      setMessages((prev) =>
        [
          ...prev.filter((m) => now - m.bornAt < lifetimeMs),
          { id: idRef.current, link, bornAt: now },
        ].slice(-8),
      );
    }, intervalMs);
    return () => clearInterval(tick);
  }, []);

  // 60 Hz repaint so the animated heads move smoothly. `now` is read
  // from state (not directly from performance.now during render) so the
  // useMemo below stays pure.
  const [now, setNow] = useState(0);
  useEffect(() => {
    let raf = 0;
    const loop = () => {
      setNow(performance.now());
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  const projectedClusters = useMemo(() => {
    if (!world) return [];
    return clusters
      .map((c) => {
        const p = world.projection(c.coords);
        return p ? { ...c, x: p[0], y: p[1] } : null;
      })
      .filter(
        (c): c is (typeof clusters)[number] & { x: number; y: number } =>
          c !== null,
      );
  }, [world]);

  const projectedMessages = useMemo(() => {
    if (!world) return [];
    return messages
      .map((m) => {
        const a = world.projection(m.link.from);
        const b = world.projection(m.link.to);
        if (!a || !b) return null;
        return {
          id: m.id,
          x1: a[0],
          y1: a[1],
          x2: b[0],
          y2: b[1],
          age: now - m.bornAt,
        };
      })
      .filter((m): m is NonNullable<typeof m> => m !== null);
  }, [messages, world, now]);

  return (
    <div
      className={`relative rounded-2xl border border-cream-400/70 bg-cream-200 p-3 shadow-[0_1px_0_rgba(20,19,18,0.02)] ${className}`}
    >
      {showChip && (
        <div className="absolute left-5 top-4 z-10 flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.2em] text-ink-400">
          <span className="inline-flex h-1.5 w-1.5 rounded-full bg-rust animate-pulse-dot" />
          Live
        </div>
      )}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="block w-full h-auto"
        style={{ overflow: "visible" }}
      >
        <defs>
          <radialGradient id="mini-msg-head" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#C45A3C" stopOpacity="0.95" />
            <stop offset="60%" stopColor="#C45A3C" stopOpacity="0.5" />
            <stop offset="100%" stopColor="#C45A3C" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* Countries */}
        {world &&
          world.countries.features.map((f: Feature, i: number) => (
            <path
              key={`mc-${i}`}
              d={world.path(f) || ""}
              fill="#EDE8DA"
              stroke="#DDD7C5"
              strokeWidth={0.5}
            />
          ))}

        {/* Animated message edges */}
        {projectedMessages.map((m) => {
          const drawDur = 1100;
          const t = Math.min(1, m.age / drawDur);
          const ease = 1 - Math.pow(1 - t, 3);
          const hx = m.x1 + (m.x2 - m.x1) * ease;
          const hy = m.y1 + (m.y2 - m.y1) * ease;
          const fade =
            m.age <= drawDur ? 1 : Math.max(0, 1 - (m.age - drawDur) / 1300);

          return (
            <g key={`mm-${m.id}`} opacity={fade}>
              <line
                x1={m.x1}
                y1={m.y1}
                x2={hx}
                y2={hy}
                stroke="#C45A3C"
                strokeWidth={0.8}
                strokeOpacity={0.5}
                strokeLinecap="round"
              />
              <circle cx={hx} cy={hy} r={2.6} fill="url(#mini-msg-head)" />
            </g>
          );
        })}

        {/* Cluster dots */}
        {projectedClusters.map((c) => (
          <g key={`mc-${c.city}`}>
            <circle cx={c.x} cy={c.y} r={7} fill="#C45A3C" opacity={0.12} />
            <circle cx={c.x} cy={c.y} r={2.4} fill="#C45A3C" />
          </g>
        ))}
      </svg>
    </div>
  );
}
