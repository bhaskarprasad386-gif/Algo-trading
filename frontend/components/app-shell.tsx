"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Bell, Bot, BriefcaseBusiness, ChartNoAxesCombined, Clock3, Home, Menu, Settings2, WalletCards, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { themeOptions, useTheme, type ThemeName } from "@/components/theme-provider";

const nav = [
  { href: "/", label: "Command Center", icon: Home },
  { href: "/scanner", label: "Live Scanner", icon: Activity },
  { href: "/strategies/calendar-spread", label: "Calendar Scanner", icon: Activity },
  { href: "/strategies/synthetic-arbitrage", label: "Synthetic Scanner", icon: Activity },
  { href: "/strategies/box-spread", label: "Box Scanner", icon: Activity },
  { href: "/strategies/custom-strategy", label: "Custom Scanner", icon: Activity },
  { href: "/custom-alert", label: "Custom Alert", icon: Bell },
  { href: "/paper-trading", label: "Paper Trading", icon: WalletCards },
  { href: "/auto-paper", label: "Auto Paper Trading", icon: Bot },
  { href: "/positions", label: "Positions", icon: BriefcaseBusiness },
  { href: "/completed-trades", label: "Completed Trades", icon: ChartNoAxesCombined },
  { href: "/history", label: "History", icon: Clock3 },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { theme, setTheme } = useTheme();

  return (
    <div className="min-h-screen theme-bg theme-text">
      <header className="sticky top-0 z-40 border-b theme-border theme-surface backdrop-blur">
        <div className="flex h-16 items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-3">
            <button
              className="rounded-lg border theme-border p-2 theme-muted lg:hidden"
              onClick={() => setMobileOpen((v) => !v)}
              aria-label="Toggle navigation"
            >
              {mobileOpen ? <X size={19} /> : <Menu size={19} />}
            </button>
            <Link href="/" className="flex items-center gap-3" onClick={() => setMobileOpen(false)}>
              <span className="grid h-9 w-9 place-items-center rounded-xl theme-accent-bg theme-accent">
                <ChartNoAxesCombined size={20} />
              </span>
              <div>
                <div className="text-sm font-semibold theme-text">ALGO TRADING</div>
                <div className="text-[10px] uppercase tracking-[0.18em] theme-subtle">Command Terminal</div>
              </div>
            </Link>
          </div>

          <div className="hidden items-center gap-3 sm:flex">
            <ThemePicker theme={theme} setTheme={setTheme} />
            <Link href="/" className="text-xs font-medium theme-muted">
              Live status
            </Link>
            <div className="rounded-lg border theme-border theme-warning-bg px-3 py-2 text-xs font-semibold theme-warning">
              PAPER MODE · LIVE ORDERS OFF
            </div>
          </div>
          <button className="min-h-10 min-w-10 rounded-lg p-2 theme-muted" aria-label="Settings">
            <Settings2 size={19} />
          </button>
        </div>
      </header>

      <div className="flex">
        <aside className="fixed inset-y-16 left-0 z-30 hidden w-64 border-r theme-border theme-surface lg:block">
          <Navigation pathname={pathname} />
        </aside>

        <div
          className={`fixed inset-16 inset-x-0 z-20 theme-overlay transition-opacity duration-200 lg:hidden ${mobileOpen ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"}`}
          onClick={() => setMobileOpen(false)}
          aria-hidden={!mobileOpen}
        >
          <aside
            className={`h-full w-[min(18rem,86vw)] border-r theme-border theme-surface theme-shadow transition-transform duration-200 ease-out ${mobileOpen ? "translate-x-0" : "-translate-x-full"}`}
            onClick={(e) => e.stopPropagation()}
            aria-label="Mobile navigation"
          >
            <Navigation pathname={pathname} onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>

        <main className="min-h-[calc(100vh-4rem)] w-full min-w-0 lg:ml-64">
          <div className="mx-auto w-full max-w-[1600px] p-4 pb-24 sm:p-6 lg:p-8">{children}</div>
        </main>
      </div>

      <nav className="fixed inset-x-0 bottom-0 z-40 border-t theme-border theme-surface px-2 py-2 backdrop-blur lg:hidden">
        <div className="mx-auto flex max-w-lg items-center justify-around">
          {nav.slice(0, 5).map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href + "/"));
            return (
              <Link key={item.href} href={item.href} className={`flex min-h-11 min-w-14 flex-col items-center gap-1 rounded-lg px-2 py-1.5 text-[10px] ${active ? "theme-accent" : "theme-muted"}`}>
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


function ThemePicker({ theme, setTheme }: { theme: ThemeName; setTheme: (theme: ThemeName) => void }) {
  return (
    <label className="inline-flex items-center gap-2 rounded-xl border theme-border theme-surface-2 px-2.5 py-1.5 text-xs">
      <Settings2 size={14} className="theme-accent" aria-hidden="true" />
      <span className="theme-subtle">Theme</span>
      <select
        value={theme}
        onChange={(event) => setTheme(event.target.value as ThemeName)}
        className="min-h-8 border-0 bg-transparent px-1 py-0 text-xs font-semibold theme-text outline-none"
        aria-label="Choose visual theme"
      >
        {themeOptions.map((item) => (
          <option key={item.id} value={item.id}>{item.symbol} {item.label}</option>
        ))}
      </select>
    </label>
  );
}

function Navigation({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  return (
    <nav className="flex h-full flex-col p-4">
      <div className="mb-3 px-3 text-[10px] font-semibold uppercase tracking-[0.18em] theme-subtle">Workspace</div>
      <div className="space-y-1">
        {nav.map((item) => {
          const Icon = item.icon;
          const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href + "/"));
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              className={`flex items-center gap-3 rounded-xl px-3 py-3 text-sm transition ${active ? "theme-accent-bg theme-accent" : "theme-muted"}`}
            >
              <Icon size={18} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </div>
      <div className="mt-auto rounded-2xl border theme-border theme-surface-2 p-4">
        <div className="text-xs font-medium theme-text">System foundation</div>
        <p className="mt-1 text-xs leading-5 theme-muted">Frontend shell only. Trading logic remains in the existing FastAPI backend.</p>
      </div>
    </nav>
  );
}
