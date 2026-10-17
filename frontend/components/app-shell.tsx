"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Bell, ChartNoAxesCombined, Home, Menu, Settings2, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { themeOptions, useTheme, type ThemeName } from "@/components/theme-provider";

const nav = [
  { href: "/", label: "Dashboard", icon: Home },
  { href: "/scanner", label: "Cash Future", icon: Activity },
  { href: "/strategies/calendar-spread", label: "Calendar Scanner", icon: Activity },
  { href: "/strategies/synthetic-arbitrage", label: "Synthetic Scanner", icon: Activity },
  { href: "/strategies/box-spread", label: "Box Scanner", icon: Activity },
  { href: "/strategies/custom-strategy", label: "Custom Scanner", icon: Activity },
  { href: "/custom-alert", label: "Custom Alert", icon: Bell },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { theme, setTheme } = useTheme();

  return (
    <div className="min-h-screen min-h-[100dvh] theme-bg theme-text">
      <header className="app-topbar sticky top-0 z-40 border-b theme-border theme-surface backdrop-blur">
        <div className="flex h-[64px] items-center justify-between px-3 sm:px-6">
          <div className="flex items-center gap-3">
            <button className="rounded-lg border theme-border p-2 theme-muted lg:hidden" onClick={() => setMobileOpen((v) => !v)} aria-label="Toggle navigation">
              {mobileOpen ? <X size={18} /> : <Menu size={18} />}
            </button>
            <Link href="/" className="flex items-center gap-2.5" onClick={() => setMobileOpen(false)}>
              <span className="grid h-10 w-10 place-items-center rounded-xl theme-accent-bg theme-accent">
                <ChartNoAxesCombined size={19} />
              </span>
              <div>
                <div className="text-[13px] font-extrabold tracking-[0.08em] theme-text">ALGO TRADING</div>
                <div className="mt-0.5 text-[10px] leading-3 theme-subtle">Live Market Monitor</div>
              </div>
            </Link>
          </div>

          <div className="flex items-center gap-2 sm:gap-3">
            <span className="hidden items-center gap-2 rounded-full border theme-border theme-surface-2 px-3 py-2 text-[10px] font-semibold theme-muted md:inline-flex">
              <Settings2 size={12} /> Dashboard Controls
            </span>
            <ThemePicker theme={theme} setTheme={setTheme} />
          </div>
        </div>
      </header>

      <div className="flex">
        <aside className="app-sidebar fixed inset-y-[64px] left-0 z-30 hidden w-[236px] border-r theme-border theme-surface lg:block">
          <Navigation pathname={pathname} />
        </aside>

        <div className={`fixed inset-x-0 bottom-0 top-[64px] z-20 theme-overlay transition-opacity duration-200 lg:hidden ${mobileOpen ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"}`} onClick={() => setMobileOpen(false)} aria-hidden={!mobileOpen}>
          <aside className={`h-full w-[min(18rem,86vw)] border-r theme-border theme-surface theme-shadow transition-transform duration-200 ease-out ${mobileOpen ? "translate-x-0" : "-translate-x-full"}`} onClick={(e) => e.stopPropagation()} aria-label="Mobile navigation">
            <Navigation pathname={pathname} onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>

        <main className="min-h-[calc(100vh-4rem)] min-h-[calc(100dvh-4rem)] w-full min-w-0 lg:ml-[236px]">
          <div className="mx-auto w-full max-w-[1880px] p-3 pb-[calc(6rem+env(safe-area-inset-bottom))] sm:p-5 sm:pb-5 lg:p-6">{children}</div>
        </main>
      </div>

      <nav className="app-mobile-nav fixed inset-x-0 bottom-0 z-40 border-t theme-border theme-surface px-2 pt-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))] backdrop-blur lg:hidden" aria-label="Primary navigation">
        <div className="mx-auto flex max-w-lg items-center gap-1 overflow-x-auto">
          {nav.map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href + "/"));
            return <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined} className={`flex min-h-11 min-w-[4.25rem] shrink-0 flex-col items-center justify-center gap-1 rounded-lg px-2 py-1.5 text-[10px] ${active ? "theme-accent" : "theme-muted"}`}><Icon size={18} /><span className="whitespace-nowrap">{item.label.split(" ")[0]}</span></Link>;
          })}
        </div>
      </nav>
    </div>
  );
}

function ThemePicker({ theme, setTheme }: { theme: ThemeName; setTheme: (theme: ThemeName) => void }) {
  return (
    <label className="inline-flex items-center gap-2 rounded-lg border theme-border theme-surface-2 px-2 py-1 text-[10px]">
      <Settings2 size={13} className="theme-accent" aria-hidden="true" />
      <span className="theme-subtle">Theme</span>
      <select value={theme} onChange={(event) => setTheme(event.target.value as ThemeName)} className="min-h-7 border-0 bg-transparent px-1 py-0 text-[10px] font-semibold theme-text outline-none" aria-label="Choose visual theme">
        {themeOptions.map((item) => <option key={item.id} value={item.id}>{item.symbol} {item.label}</option>)}
      </select>
    </label>
  );
}

function Navigation({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  return (
    <nav className="app-navigation flex h-full flex-col p-3">
      <div className="mb-3 px-2.5 pt-3 text-[10px] font-bold uppercase tracking-[0.2em] theme-subtle">Workspace</div>
      <div className="space-y-1">
        {nav.map((item) => {
          const Icon = item.icon;
          const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href + "/"));
          return <Link key={item.href} href={item.href} onClick={onNavigate} className={`app-nav-link flex items-center gap-3 rounded-xl px-3 py-3 text-[12px] font-medium transition ${active ? "theme-accent-bg theme-accent" : "theme-muted hover:theme-surface-2"}`}><Icon size={17} /><span>{item.label}</span></Link>;
        })}
      </div>
      <div className="mt-auto rounded-2xl border theme-border theme-surface-2 p-4">
        <div className="text-[11px] font-semibold theme-text">System foundation</div>
        <p className="mt-1 text-[10px] leading-4 theme-muted">Frontend shell only. Trading logic remains in the existing FastAPI backend.</p>
      </div>
    </nav>
  );
}
