import Link from "next/link";

export function Footer() {
  return (
    <footer className="border-t border-cream-400/70 bg-cream-100">
      <div className="mx-auto max-w-[1240px] px-6 sm:px-10 pt-20 pb-12">
        <div className="grid gap-12 lg:grid-cols-[1.5fr_1fr_1fr_1fr]">
          <div>
            <Link
              href="/"
              className="font-display text-2xl tracking-tight text-ink-900"
            >
              n<span className="text-rust">/</span>est
            </Link>
            <p className="mt-5 max-w-xs text-[0.95rem] leading-relaxed text-ink-400">
              A discrete-event testbed for multi-agent protocols.
              Built at MIT Media Lab as part of Project NANDA.
            </p>
            <p className="mt-8 font-mono text-[10px] uppercase tracking-[0.22em] text-ink-300">
              Apache 2.0 &middot; {new Date().getFullYear()}
            </p>
          </div>

          <FooterColumn title="Platform">
            <FooterLink href="/agents">Agents</FooterLink>
            <FooterLink href="/experiments">Experiments</FooterLink>
            <FooterLink href="/leaderboard">Leaderboard</FooterLink>
            <FooterLink href="/visualizer">Visualizer</FooterLink>
          </FooterColumn>

          <FooterColumn title="Resources">
            <FooterLink href="/docs">Documentation</FooterLink>
            <FooterLink href="https://github.com/mariagorskikh/nest" external>
              GitHub
            </FooterLink>
            <FooterLink href="https://projectnanda.org" external>
              Project NANDA
            </FooterLink>
          </FooterColumn>

          <FooterColumn title="Community">
            <FooterLink
              href="https://github.com/mariagorskikh/nest/issues"
              external
            >
              Report an issue
            </FooterLink>
            <FooterLink
              href="https://github.com/mariagorskikh/nest/discussions"
              external
            >
              Discussions
            </FooterLink>
          </FooterColumn>
        </div>

        <div className="mt-16 border-t border-cream-400/70 pt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-[0.8rem] text-ink-300">
            &copy; {new Date().getFullYear()} NEST · An open testbed for the
            agentic web.
          </p>
          <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-ink-300">
            Made at MIT Media Lab
          </p>
        </div>
      </div>
    </footer>
  );
}

function FooterColumn({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h3 className="font-mono text-[10px] uppercase tracking-[0.22em] text-ink-300">
        {title}
      </h3>
      <ul className="mt-5 space-y-3">{children}</ul>
    </div>
  );
}

function FooterLink({
  href,
  external,
  children,
}: {
  href: string;
  external?: boolean;
  children: React.ReactNode;
}) {
  if (external) {
    return (
      <li>
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[0.95rem] text-ink-500 hover:text-ink-900 transition-colors"
        >
          {children}
        </a>
      </li>
    );
  }
  return (
    <li>
      <Link
        href={href}
        className="text-[0.95rem] text-ink-500 hover:text-ink-900 transition-colors"
      >
        {children}
      </Link>
    </li>
  );
}
