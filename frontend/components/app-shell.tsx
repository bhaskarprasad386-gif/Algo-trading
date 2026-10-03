"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Bell, BriefcaseBusiness, ChartNoAxesCombined, Clock3, Home, Menu, Settings2, WalletCards, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { StatusDot } from "./ui";

const nav = [
  { href: "/", label: "Command Center", icon: Home },
  { href: "/scanner", label: "Live Scanner", icon: Activity },
  { href: "/custom-alert", label: "Custom Alert", icon: Bell },
  { href: "/paper-trading", label: "Paper Trading", icon: WalletCards },
  { href: "/positions", label: "Positions", icon: BriefcaseBusiness },
  { href: "/completed-trades", label: "Completed Trades", icon: ChartNoAxesCombined },
  { href: "/history", label: "History", icon: Clock3 },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="min-h-screen bg-algo-bg">
      <header className="sticky top-0 z-40 border-b border-algo-border bg-algo-bg/95 backdrop-blur">
        <div className="flex h-16 items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-3">
            <button
              className="rounded-lg border border-algo-border p-2 text-algo-muted hover:text-white lg:hidden"
              onClick={() => setMobileOpen((v) => !v)}
              aria-label="Toggle navigation"
            >
              {mobileOpen ? <X size={19} /> : <Menu size={19} />}
            </button>
            <Link href="/" className="flex items-center gap-3" onClick={() => setMobileOpen(false)}>
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-algo-primary/10 text-algo-primary">
                <ChartNoAxesCombined size={20} />
              </span>
              <div>
                <div className="text-sm font-semibold text-white">ALGO TRADING</div>
                <div className="text-[10px] uppercase tracking-[0.18em] text-algo-muted">Command Terminal</div>
              </div>
            </Link>
          </div>

          <div className="hidden items-center gap-5 sm:flex">
            <div className="flex items-center gap-2 text-xs text-algo-muted">
              <StatusDot live={false} />
              Market data connection · not wired
            </div>
            <div className="rounded-lg border border-algo-border bg-algo-surface px-3 py-2 text-xs font-medium text-algo-warning">
              PAPER MODE · LIVE ORDERS OFF
            </div>
          </div>
          <button className="rounded-lg p-2 text-algo-muted hover:text-white" aria-label="Settings">
            <Settings2 size={19} />
          </button>
        </div>
      </header>

      <div className="flex">
        <aside className="fixed inset-y-16 left-0 z-30 hidden w-64 border-r border-algo-border bg-algo-surface lg:block">
          <Navigation pathname={pathname} />
        </aside>

        {mobileOpen ? (
          <div className="fixed inset-16 inset-x-0 z-20 bg-black/60 lg:hidden" onClick={() => setMobileOpen(false)}>
            <aside className="h-full w-72 border-r border-algo-border bg-algo-surface" onClick={(e) => e.stopPropagation()}>
              <Navigation pathname={pathname} onNavigate={() => setMobileOpen(false)} />
            </aside>
          </div>
        ) : null}

        <main className="min-h-[calc(100vh-4rem)] w-full lg:ml-64">
          <div className="mx-auto w-full max-w-[1600px] p-4 pb-24 sm:p-6 lg:p-8">{children}</div>
        </main>
      </div>

      <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-algo-border bg-algo-surface/95 px-2 py-2 backdrop-blur lg:hidden">
        <div className="mx-auto flex max-w-lg items-center justify-around">
          {nav.slice(0, 5).map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href;
            return (
              <Link key={item.href} href={item.href} className={`flex min-w-14 flex-col items-center gap-1 rounded-lg px-2 py-1.5 text-[10px] ${active ? "text-algo-primary" : "text-algo-muted"}`}>
                <Icon size={18} />
                <span>{item.label.split(" ")[0]}</span>
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}

function Navigation({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  return (
    <nav className="flex h-full flex-col p-4">
      <div className="mb-3 px-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-algo-muted">Workspace</div>
      <div className="space-y-1">
        {nav.map((item) => {
          const Icon = item.icon;
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              className={`flex items-center gap-3 rounded-xl px-3 py-3 text-sm transition ${active ? "bg-algo-primary/10 text-algo-primary" : "text-algo-muted hover:bg-white/[.03] hover:text-white"}`}
            >
              <Icon size={18} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </div>
      <div className="mt-auto rounded-2xl border border-algo-border bg-algo-card p-4">
        <div className="text-xs font-medium text-white">System foundation</div>
        <p className="mt-1 text-xs leading-5 text-algo-muted">Frontend shell only. Trading logic remains in the existing FastAPI backend.</p>
      </div>
    </nav>
  );
}
