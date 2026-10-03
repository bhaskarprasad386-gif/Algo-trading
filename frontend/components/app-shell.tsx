"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Bell, Bot, BriefcaseBusiness, ChartNoAxesCombined, Clock3, Home, Menu, Settings2, WalletCards, X } from "lucide-react";
import { useState, type ReactNode } from "react";

const nav = [
  { href: "/", label: "Command Center", icon: Home },
  { href: "/scanner", label: "Live Scanner", icon: Activity },
  { href: "/strategies/cash-future", label: "Cash-Future Scanner", icon: Activity },
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

  return (
    <div className="min-h-screen bg-white text-slate-900">
      <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="flex h-16 items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-3">
            <button
              className="rounded-lg border border-slate-200 p-2 text-slate-600 hover:bg-slate-50 lg:hidden"
              onClick={() => setMobileOpen((v) => !v)}
              aria-label="Toggle navigation"
            >
              {mobileOpen ? <X size={19} /> : <Menu size={19} />}
            </button>
            <Link href="/" className="flex items-center gap-3" onClick={() => setMobileOpen(false)}>
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-sky-50 text-sky-600">
                <ChartNoAxesCombined size={20} />
              </span>
              <div>
                <div className="text-sm font-semibold text-slate-900">ALGO TRADING</div>
                <div className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Command Terminal</div>
              </div>
            </Link>
          </div>

          <div className="hidden items-center gap-3 sm:flex">
            <Link href="/" className="text-xs font-medium text-slate-500 hover:text-slate-900">
              Live status
            </Link>
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-700">
              PAPER MODE · LIVE ORDERS OFF
            </div>
          </div>
          <button className="min-h-10 min-w-10 rounded-lg p-2 text-slate-500 hover:bg-slate-50 hover:text-slate-900" aria-label="Settings">
            <Settings2 size={19} />
          </button>
        </div>
      </header>

      <div className="flex">
        <aside className="fixed inset-y-16 left-0 z-30 hidden w-64 border-r border-slate-200 bg-white lg:block">
          <Navigation pathname={pathname} />
        </aside>

        <div
          className={`fixed inset-16 inset-x-0 z-20 bg-slate-900/20 transition-opacity duration-200 lg:hidden ${mobileOpen ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"}`}
          onClick={() => setMobileOpen(false)}
          aria-hidden={!mobileOpen}
        >
          <aside
            className={`h-full w-[min(18rem,86vw)] border-r border-slate-200 bg-white shadow-2xl transition-transform duration-200 ease-out ${mobileOpen ? "translate-x-0" : "-translate-x-full"}`}
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

      <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-slate-200 bg-white/95 px-2 py-2 backdrop-blur lg:hidden">
        <div className="mx-auto flex max-w-lg items-center justify-around">
          {nav.slice(0, 5).map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href + "/"));
            return (
              <Link key={item.href} href={item.href} className={`flex min-h-11 min-w-14 flex-col items-center gap-1 rounded-lg px-2 py-1.5 text-[10px] ${active ? "text-sky-600" : "text-slate-500"}`}>
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
      <div className="mb-3 px-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">Workspace</div>
      <div className="space-y-1">
        {nav.map((item) => {
          const Icon = item.icon;
          const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href + "/"));
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              className={`flex items-center gap-3 rounded-xl px-3 py-3 text-sm transition ${active ? "bg-sky-50 text-sky-700" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"}`}
            >
              <Icon size={18} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </div>
      <div className="mt-auto rounded-2xl border border-slate-200 bg-slate-50 p-4">
        <div className="text-xs font-medium text-slate-900">System foundation</div>
        <p className="mt-1 text-xs leading-5 text-slate-500">Frontend shell only. Trading logic remains in the existing FastAPI backend.</p>
      </div>
    </nav>
  );
}
