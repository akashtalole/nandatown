"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/agents", label: "Agents" },
  { href: "/experiments", label: "Experiments" },
  { href: "/leaderboard", label: "Leaderboard" },
  { href: "/visualizer", label: "Visualizer" },
  { href: "/docs", label: "Docs" },
];

export function Navbar() {
  const pathname = usePathname();

  return (
    <nav className="sticky top-0 z-50 border-b border-cream-400/60 bg-cream-100/85 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[1240px] items-center justify-between px-6 sm:px-10">
        {/* Wordmark — uses a slashed N reminiscent of Anthropic's wordmark */}
        <Link
          href="/"
          className="flex items-baseline gap-2 group"
          aria-label="NEST — home"
        >
          <span className="font-display text-[1.4rem] leading-none tracking-tight text-ink-900">
            n<span className="text-rust">/</span>est
          </span>
          <span className="hidden sm:inline-block pl-3 ml-1 border-l border-cream-400 text-[10px] font-mono uppercase tracking-[0.2em] text-ink-300 leading-none">
            Project NANDA
          </span>
        </Link>

        {/* Main nav */}
        <div className="hidden md:flex items-center gap-1">
          {links.map((link) => {
            const isActive = pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`relative px-4 py-2 text-[0.92rem] font-medium transition-colors ${
                  isActive
                    ? "text-ink-900"
                    : "text-ink-400 hover:text-ink-900"
                }`}
              >
                {link.label}
                {isActive && (
                  <span className="absolute left-3 right-3 -bottom-px h-px bg-ink-900" />
                )}
              </Link>
            );
          })}
        </div>

        <div className="flex items-center gap-2">
          <a
            href="https://github.com/mariagorskikh/nest"
            target="_blank"
            rel="noopener noreferrer"
            className="hidden sm:inline-flex items-center text-[0.85rem] font-medium text-ink-500 hover:text-ink-900 px-3 py-2 transition-colors"
          >
            GitHub
          </a>
          <Link
            href="/experiments"
            className="inline-flex items-center rounded-md bg-ink-900 text-cream-50 px-4 py-2 text-[0.85rem] font-medium tracking-tight transition-colors hover:bg-ink-700"
          >
            Try NEST
          </Link>
        </div>
      </div>
    </nav>
  );
}
