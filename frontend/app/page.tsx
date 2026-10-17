"use client";

import { useEffect, useState } from "react";
import { Activity, BarChart3, Bell, Clock3, Database, Gauge, Radio, TriangleAlert, TrendingDown, TrendingUp, Wallet, Zap } from "lucide-react";
import Link from "next/link";
import { Card } from "@/components/ui";
import { appConfig, getDashboardWsUrl } from "@/lib/config";

const markets = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"];
const strategies = [
  { name: "Cash Future", detail: "Live Scanner • F&O •", href: "/scanner", icon: Radio },
  { name: "Calendar Spread", detail: "Time spread • NIFTY •", href: "/strategies/calendar-spread", icon: Activity },
  { name: "Synthetic Arbitrage", detail: "Cash-Synth • Arbitrage •", href: "/strategies/synthetic-arbitrage", icon: Zap },
  { name: "Box Spread", detail: "Options • Box •", href: "/strategies/box-spread", icon: Gauge },
  { name: "Custom Strategy", detail: "Independent • User-defined •", href: "/strategies/custom-strategy", icon: Activity },
  { name: "Strategy Scanner", detail: "Dedicated workspaces •", href: "/scanner", icon: Radio },
];

export default function HomePage() {
  const [apiStatus, setApiStatus] = useState<"checking" | "connected" | "error">("checking");
  const [wsStatus, setWsStatus] = useState<"connecting" | "connected" | "error">("connecting");
  const [snapshot, setSnapshot] = useState({ calendar: 0, synthetic: 0, box: 0, opportunities: 0, timestamp: null as string | null, cashFutureObservedAt: null as string | null });
  const [indexLtps, setIndexLtps] = useState<Record<string, { ltp: number | null; previousClose: number | null; changePercent: number | null; status: string }>>({});
  const [marketSession, setMarketSession] = useState<"OPEN" | "CLOSED" | "UNKNOWN">("UNKNOWN");
  const [mcxSession, setMcxSession] = useState<"OPEN" | "CLOSED" | "UNKNOWN">("UNKNOWN");
  const [marketCheckedAt, setMarketCheckedAt] = useState<string | null>(null);
  const [feedStatus, setFeedStatus] = useState<"checking" | "live" | "stale" | "no-data" | "closed" | "error">("checking");
  const [feedAge, setFeedAge] = useState<number | null>(null);
  const [runtime, setRuntime] = useState<{ticks: number; runtimeStartedAt: number | null; activeInstruments: number; socketGroups: number; connectedGroups: number; maxSocketSessions: number; consumers: string[]; deliveryErrors: number; normalizerErrors: number; runners: Record<string, {running: boolean; detail: string; updates: number; payloads: number; instruments: number}>}>({ticks: 0, runtimeStartedAt: null, activeInstruments: 0, socketGroups: 0, connectedGroups: 0, maxSocketSessions: 3, consumers: [], deliveryErrors: 0, normalizerErrors: 0, runners: {}});
  const [connectionsOpen, setConnectionsOpen] = useState(false);
  const [diagnosticErrors, setDiagnosticErrors] = useState<any[]>([]);
  const [diagnosticCount, setDiagnosticCount] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    fetch(appConfig.apiBaseUrl.replace(/\/$/, "") + "/health", { cache: "no-store", signal: controller.signal })
      .then((response) => { if (!response.ok) throw new Error("health"); setApiStatus("connected"); })
      .catch(() => setApiStatus("error"));
    return (
    <main className="min-w-0 space-y-6 pb-8">
      <section className="relative overflow-hidden rounded-3xl border theme-border theme-surface-2 p-5 sm:p-7">
        <div className="pointer-events-none absolute -right-12 -top-16 h-56 w-56 rounded-full opacity-20 blur-3xl theme-accent-bg" />
        <div className="relative flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
          <div className="min-w-0">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-2 rounded-full border theme-border px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.16em] theme-accent"><span className="h-1.5 w-1.5 animate-pulse rounded-full theme-accent-bg" /> Trading Terminal</span>
              <span className="rounded-full border theme-border px-3 py-1.5 text-[10px] font-semibold theme-muted">MARKET DATA ONLY</span>
            </div>
            <h1 className="text-3xl font-black tracking-tight theme-text sm:text-4xl">Market <span className="theme-accent">Command</span></h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 theme-muted">One workspace for index movement, scanner opportunities and live infrastructure health.</p>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:flex">
            <div className="min-w-32 rounded-2xl border theme-border theme-surface px-4 py-3">
              <div className="text-[9px] font-bold uppercase tracking-[0.15em] theme-subtle">API</div>
              <div className={"mt-1 flex items-center gap-2 text-xs font-bold " + (apiStatus === "connected" ? "theme-success" : apiStatus === "error" ? "theme-danger" : "theme-warning")}><span className="h-2 w-2 rounded-full theme-success-bg" />{apiLabel}</div>
            </div>
            <div className="min-w-32 rounded-2xl border theme-border theme-surface px-4 py-3">
              <div className="text-[9px] font-bold uppercase tracking-[0.15em] theme-subtle">Dashboard WS</div>
              <div className={"mt-1 flex items-center gap-2 text-xs font-bold " + (wsStatus === "connected" ? "theme-success" : wsStatus === "error" ? "theme-danger" : "theme-warning")}><span className="h-2 w-2 rounded-full theme-accent-bg" />{wsLabel}</div>
            </div>
          </div>
        </div>
        <div className="relative mt-6 grid grid-cols-2 gap-3 border-t theme-border pt-5 sm:grid-cols-4">
          <div><div className="text-[9px] font-bold uppercase tracking-[0.14em] theme-subtle">NSE Session</div><div className={"mt-1 text-lg font-black " + (marketSession === "OPEN" ? "theme-success" : marketSession === "CLOSED" ? "theme-warning" : "theme-muted")}>{marketSession}</div><div className="text-[10px] theme-subtle">Equity market</div></div>
          <div><div className="text-[9px] font-bold uppercase tracking-[0.14em] theme-subtle">MCX Session</div><div className={"mt-1 text-lg font-black " + (mcxSession === "OPEN" ? "theme-success" : mcxSession === "CLOSED" ? "theme-warning" : "theme-muted")}>{mcxSession}</div><div className="text-[10px] theme-subtle">Commodity market</div></div>
          <div><div className="text-[9px] font-bold uppercase tracking-[0.14em] theme-subtle">Market Feed</div><div className={"mt-1 text-lg font-black " + (feedStatus === "live" ? "theme-success" : feedStatus === "error" || feedStatus === "stale" ? "theme-danger" : "theme-warning")}>{feedLabel}</div><div className="text-[10px] theme-subtle">Age: {feedAgeLabel}</div></div>
          <div><div className="text-[9px] font-bold uppercase tracking-[0.14em] theme-subtle">Broker Orders</div><div className="mt-1 text-lg font-black theme-warning">OFF</div><div className="text-[10px] theme-subtle">No live order routing</div></div>
        </div>
      </section>

      <section aria-label="Market overview">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div><div className="text-[10px] font-bold uppercase tracking-[0.18em] theme-accent">01 / Markets</div><h2 className="mt-1 text-lg font-bold">Index Overview</h2></div>
          <span className="text-[10px] theme-subtle">{marketDataAge == null ? "Waiting for overview" : "Updated " + Math.round(marketDataAge) + "s ago"}</span>
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-6">
          {markets.map((symbol) => {
            const quote = indexLtps[symbol];
            const quoteAvailable = marketDataAge != null && marketDataAge <= 15 && (marketSession === "OPEN" ? quote?.status === "LIVE" : marketSession === "CLOSED" ? quote?.status === "CLOSED_LAST_CLOSE" : false);
            const change = quoteAvailable ? quote?.changePercent : null;
            const positive = change != null && change >= 0;
            return (
              <Card key={symbol} className="group relative overflow-hidden rounded-2xl border theme-border theme-surface-2 p-4 transition duration-200 hover:-translate-y-0.5">
                <div className={"absolute inset-x-0 top-0 h-0.5 " + (change == null ? "theme-border" : positive ? "theme-success-bg" : "theme-danger-bg")} />
                <div className="flex items-center justify-between gap-2"><span className="text-xs font-extrabold tracking-wide">{symbol}</span><span className="h-2 w-2 rounded-full" style={{ backgroundColor: quoteAvailable ? (positive ? "#10B981" : "#EF4444") : "#64748B" }} /></div>
                <div className="mt-4 text-xl font-black tabular-nums tracking-tight">{quoteAvailable && quote?.ltp != null ? quote.ltp.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—"}</div>
                <div className={"mt-2 inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[10px] font-bold " + (change == null ? "theme-surface theme-muted" : positive ? "theme-success-bg theme-success" : "theme-danger-bg theme-danger")}>
                  {change == null ? "QUOTE UNAVAILABLE" : <>{positive ? <TrendingUp size={12} /> : <TrendingDown size={12} />}{change > 0 ? "+" : ""}{change.toFixed(2)}%</>}
                </div>
              </Card>
            );
          })}
        </div>
      </section>

      <section aria-label="Quick stats" className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Card className="rounded-2xl border theme-border theme-surface-2 p-4"><div className="flex items-center justify-between"><span className="text-[10px] font-bold uppercase tracking-wider theme-subtle">Cash-Future Signals</span><Radio size={16} className="theme-accent" /></div><div className="mt-3 text-3xl font-black tabular-nums">{snapshot.opportunities.toLocaleString("en-IN")}</div><div className="mt-1 text-[10px] theme-muted">Latest dashboard snapshot</div></Card>
        <Card className="rounded-2xl border theme-border theme-surface-2 p-4"><div className="flex items-center justify-between"><span className="text-[10px] font-bold uppercase tracking-wider theme-subtle">Common Feed Ticks</span><Activity size={16} className="theme-accent" /></div><div className="mt-3 text-3xl font-black tabular-nums">{runtime.ticks.toLocaleString("en-IN")}</div><div className="mt-1 text-[10px] theme-muted">{runtime.connectedGroups}/{runtime.maxSocketSessions} socket groups connected</div></Card>
        <Card className="rounded-2xl border theme-border theme-surface-2 p-4"><div className="flex items-center justify-between"><span className="text-[10px] font-bold uppercase tracking-wider theme-subtle">Active Instruments</span><Gauge size={16} className="theme-accent" /></div><div className="mt-3 text-3xl font-black tabular-nums">{runtime.activeInstruments.toLocaleString("en-IN")}</div><div className="mt-1 text-[10px] theme-muted">Common feed subscriptions</div></Card>
        <Card className="rounded-2xl border theme-border theme-surface-2 p-4"><div className="flex items-center justify-between"><span className="text-[10px] font-bold uppercase tracking-wider theme-subtle">Runtime Errors</span><TriangleAlert size={16} className={diagnosticCount > 0 ? "theme-danger" : "theme-success"} /></div><div className={"mt-3 text-3xl font-black tabular-nums " + (diagnosticCount > 0 ? "theme-danger" : "theme-success")}>{diagnosticCount.toLocaleString("en-IN")}</div><div className="mt-1 text-[10px] theme-muted">Recorded since API process start</div></Card>
      </section>

      <section aria-label="Scanner and strategies" className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.85fr)]">
        <Card className="min-w-0 rounded-3xl border theme-border theme-surface-2 p-4 sm:p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div><div className="text-[10px] font-bold uppercase tracking-[0.18em] theme-accent">02 / Opportunities</div><h2 className="mt-1 text-lg font-bold">Live Scanner</h2></div>
            <Link href="/scanner" className="rounded-xl theme-accent-bg px-3 py-2 text-[11px] font-bold theme-accent">Open scanner ↗</Link>
          </div>
          <div className="rounded-2xl border theme-border theme-surface p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="flex items-center gap-3"><span className="grid h-11 w-11 place-items-center rounded-xl theme-accent-bg theme-accent"><Radio size={20} /></span><div><div className="text-sm font-bold">Cash-Future Scanner</div><div className="mt-1 text-[10px] theme-muted">NSE cash vs near/current futures</div></div></div>
              <span className={"rounded-full px-2.5 py-1 text-[9px] font-extrabold " + (runtime.runners.cash_future?.running ? "theme-success-bg theme-success" : "theme-warning-bg theme-warning")}>{runtime.runners.cash_future?.running ? "RUNNING" : "STATUS UNKNOWN / STOPPED"}</span>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
              <div className="rounded-xl border theme-border px-3 py-3"><div className="text-[9px] uppercase tracking-wider theme-subtle">Signals</div><div className="mt-1 text-2xl font-black">{snapshot.opportunities.toLocaleString("en-IN")}</div></div>
              <div className="rounded-xl border theme-border px-3 py-3"><div className="text-[9px] uppercase tracking-wider theme-subtle">Observed</div><div className="mt-1 text-2xl font-black">{runtime.runners.cash_future?.updates.toLocaleString("en-IN") ?? "—"}</div></div>
              <div className="col-span-2 rounded-xl border theme-border px-3 py-3 sm:col-span-1"><div className="text-[9px] uppercase tracking-wider theme-subtle">Last Observation</div><div className="mt-2 text-xs font-bold">{observationAgeLabel}</div></div>
            </div>
            <div className="mt-3 text-[10px] leading-5 theme-muted">{runtime.runners.cash_future?.detail ?? "Runner health unavailable from the latest runtime response."}</div>
          </div>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Link href="/strategies/calendar-spread" className="rounded-2xl border theme-border p-3 transition hover:theme-surface"><div className="flex items-center gap-2"><Activity size={16} className="theme-accent" /><span className="text-xs font-bold">Calendar Spread</span></div><div className="mt-3 text-2xl font-black">{snapshot.calendar.toLocaleString("en-IN")}</div><div className="text-[10px] theme-subtle">Integration rows ↗</div></Link>
            <Link href="/strategies/synthetic-arbitrage" className="rounded-2xl border theme-border p-3 transition hover:theme-surface"><div className="flex items-center gap-2"><Zap size={16} className="theme-accent" /><span className="text-xs font-bold">Synthetic Arb.</span></div><div className="mt-3 text-2xl font-black">{snapshot.synthetic.toLocaleString("en-IN")}</div><div className="text-[10px] theme-subtle">Integration rows ↗</div></Link>
            <Link href="/strategies/box-spread" className="rounded-2xl border theme-border p-3 transition hover:theme-surface"><div className="flex items-center gap-2"><Gauge size={16} className="theme-accent" /><span className="text-xs font-bold">Box Spread</span></div><div className="mt-3 text-2xl font-black">{snapshot.box.toLocaleString("en-IN")}</div><div className="text-[10px] theme-subtle">Integration rows ↗</div></Link>
          </div>
        </Card>

        <div className="space-y-4">
          <Card className="rounded-3xl border theme-border theme-surface-2 p-4 sm:p-5">
            <div className="mb-4 flex items-center gap-2"><Wallet size={17} className="theme-accent" /><div><div className="text-[10px] font-bold uppercase tracking-[0.18em] theme-accent">03 / Portfolio</div><h2 className="mt-1 text-lg font-bold">Paper Portfolio</h2></div></div>
            <div className="rounded-2xl border theme-border theme-surface p-4">
              <div className="flex items-center justify-between gap-3"><span className="text-xs theme-muted">Execution mode</span><span className="rounded-full theme-warning-bg theme-warning px-2.5 py-1 text-[9px] font-extrabold">ORDERS OFF</span></div>
              <div className="mt-3 text-sm font-bold">Safe monitoring mode</div>
              <p className="mt-2 text-[11px] leading-5 theme-muted">No verified paper-position or P&amp;L snapshot is connected. Portfolio values are intentionally not fabricated.</p>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2"><Link href="/history" className="rounded-xl border theme-border px-3 py-3 text-center text-[11px] font-bold">History ↗</Link><Link href="/scanner" className="rounded-xl border theme-border px-3 py-3 text-center text-[11px] font-bold">Scanner ↗</Link></div>
          </Card>

          <Card className="rounded-3xl border theme-border theme-surface-2 p-4 sm:p-5">
            <div className="mb-3 flex items-center gap-2"><Bell size={17} className="theme-accent" /><div><div className="text-[10px] font-bold uppercase tracking-[0.18em] theme-accent">04 / Alerts</div><h2 className="mt-1 text-lg font-bold">Recent Alerts</h2></div></div>
            {diagnosticErrors.length === 0 ? <div className="rounded-2xl border theme-border theme-surface p-4"><div className="flex items-center gap-2 text-xs font-bold theme-success"><Activity size={15} /> No errors in latest response</div><p className="mt-2 text-[10px] leading-5 theme-muted">This only reflects the diagnostics endpoint response; it does not prove every subsystem is fault-free.</p></div> : <div className="max-h-52 space-y-2 overflow-y-auto">{diagnosticErrors.slice(0, 4).map((error, index) => <div key={error.id ?? error.time ?? index} className="rounded-xl border theme-border theme-surface p-3"><div className="flex flex-wrap justify-between gap-2 text-[10px] font-bold"><span>{error.component ?? "Runtime"} · {error.severity ?? "ERROR"}</span><span className="theme-subtle">{error.time ? new Date(error.time).toLocaleTimeString("en-IN") : "Time unavailable"}</span></div><p className="mt-1 break-words text-[11px] theme-muted">{error.message ?? error.error_type ?? "Diagnostic event returned without a message"}</p></div>)}</div>}
            <Link href="/errors" className="mt-3 inline-flex text-[11px] font-bold theme-accent">Open diagnostics ↗</Link>
          </Card>
        </div>
      </section>

      <section aria-label="Market performance and system status" className="grid min-w-0 gap-4 xl:grid-cols-2">
        <Card className="min-w-0 rounded-3xl border theme-border theme-surface-2 p-4 sm:p-5">
          <div className="mb-1 flex items-center gap-2"><BarChart3 size={17} className="theme-accent" /><div><div className="text-[10px] font-bold uppercase tracking-[0.18em] theme-accent">05 / Performance</div><h2 className="mt-1 text-lg font-bold">Market Performance</h2></div></div>
          <p className="mb-5 text-[10px] leading-5 theme-subtle">Index percentage change from the latest market-overview response; not historical price data.</p>
          <div className="space-y-4">{markets.map((symbol) => {
            const quote = indexLtps[symbol];
            const quoteAvailable = marketDataAge != null && marketDataAge <= 15 && (marketSession === "OPEN" ? quote?.status === "LIVE" : marketSession === "CLOSED" ? quote?.status === "CLOSED_LAST_CLOSE" : false);
            const change = quoteAvailable ? quote?.changePercent : null;
            const width = change == null ? 0 : Math.min(48, Math.abs(change) * 12);
            const positive = change != null && change >= 0;
            return <div key={symbol} className="grid grid-cols-[78px_minmax(0,1fr)_68px] items-center gap-3"><span className="text-[10px] font-bold">{symbol}</span><div className="relative h-2 overflow-hidden rounded-full theme-surface"><span className="absolute inset-y-0 left-1/2 w-px" style={{ backgroundColor: "var(--app-border)" }} />{change != null ? <span className={"absolute top-0 h-full rounded-full " + (positive ? "theme-success-bg" : "theme-danger-bg")} style={{ width: width + "%", left: positive ? "50%" : "calc(50% - " + width + "%)" }} /> : null}</div><span className={"text-right text-[10px] font-bold tabular-nums " + (change == null ? "theme-muted" : positive ? "theme-success" : "theme-danger")}>{change == null ? "—" : (change > 0 ? "+" : "") + change.toFixed(2) + "%"}</span></div>;
          })}</div>
          <div className="mt-5 border-t theme-border pt-3 text-[10px] theme-subtle">{marketDataAge == null ? "Waiting for market overview response." : "Overview checked " + Math.round(marketDataAge) + "s ago"} · {marketSession === "CLOSED" ? "Last-close status" : "Live session quote status required"}</div>
        </Card>

        <Card className="min-w-0 rounded-3xl border theme-border theme-surface-2 p-4 sm:p-5">
          <div className="mb-4 flex items-center justify-between gap-3"><div className="flex items-center gap-2"><Database size={17} className="theme-accent" /><div><div className="text-[10px] font-bold uppercase tracking-[0.18em] theme-accent">06 / Infrastructure</div><h2 className="mt-1 text-lg font-bold">System Status</h2></div></div><button type="button" onClick={() => setConnectionsOpen(true)} className="rounded-xl border theme-border px-3 py-2 text-[10px] font-bold">Details ↗</button></div>
          <div className="space-y-2">
            {[["FastAPI", apiLabel, apiStatus === "connected"], ["Dashboard WebSocket", wsLabel, wsStatus === "connected"], ["Common Market Feed", feedLabel, feedStatus === "live"], ["Cash-Future Runner", runtime.runners.cash_future?.running ? "RUNNING" : "NOT CONFIRMED", Boolean(runtime.runners.cash_future?.running)], ["Calendar Runner", runtime.runners.calendar_spread?.running ? "RUNNING" : "NOT CONFIRMED", Boolean(runtime.runners.calendar_spread?.running)], ["Synthetic Runner", runtime.runners.synthetic_arbitrage?.running ? "RUNNING" : "NOT CONFIRMED", Boolean(runtime.runners.synthetic_arbitrage?.running)], ["Box Spread Runner", runtime.runners.box_spread?.running ? "RUNNING" : "NOT CONFIRMED", Boolean(runtime.runners.box_spread?.running)]].map(([label, value, healthy]) => <div key={String(label)} className="flex items-center justify-between gap-3 rounded-xl border theme-border theme-surface px-3 py-3"><span className="text-[11px] theme-muted">{label}</span><span className={"inline-flex items-center gap-2 text-[10px] font-extrabold " + (healthy ? "theme-success" : "theme-warning")}><span className={"h-1.5 w-1.5 rounded-full " + (healthy ? "theme-success-bg" : "theme-warning-bg")} />{value}</span></div>)}
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2"><div className="rounded-xl border theme-border p-3"><div className="text-[9px] uppercase tracking-wider theme-subtle">Delivery Errors</div><div className="mt-1 text-lg font-black">{runtime.deliveryErrors.toLocaleString("en-IN")}</div></div><div className="rounded-xl border theme-border p-3"><div className="text-[9px] uppercase tracking-wider theme-subtle">Normalizer Errors</div><div className="mt-1 text-lg font-black">{runtime.normalizerErrors.toLocaleString("en-IN")}</div></div></div>
        </Card>
      </section>

      <section aria-label="Custom alerts and workspaces" className="grid gap-4 md:grid-cols-2">
        <Card className="flex items-center justify-between gap-4 rounded-2xl border theme-border theme-surface-2 p-4"><div className="flex min-w-0 items-center gap-3"><span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl theme-accent-bg theme-accent"><Bell size={18} /></span><div><h2 className="text-sm font-bold">Custom Alerts</h2><p className="mt-1 text-[10px] theme-muted">Manage configured scanner alerts.</p></div></div><Link href="/custom-alert" className="shrink-0 rounded-xl border theme-border px-3 py-2.5 text-[10px] font-bold">Manage ↗</Link></Card>
        <Card className="flex items-center justify-between gap-4 rounded-2xl border theme-border theme-surface-2 p-4"><div className="flex min-w-0 items-center gap-3"><span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl theme-accent-bg theme-accent"><Clock3 size={18} /></span><div><h2 className="text-sm font-bold">Quick Navigation</h2><p className="mt-1 text-[10px] theme-muted">Jump straight to a workspace.</p></div></div><div className="flex flex-wrap justify-end gap-2"><Link href="/scanner" className="rounded-lg border theme-border px-2.5 py-2 text-[10px] font-semibold">Scanner</Link><Link href="/strategies/calendar-spread" className="rounded-lg border theme-border px-2.5 py-2 text-[10px] font-semibold">Calendar</Link><Link href="/strategies/synthetic-arbitrage" className="rounded-lg border theme-border px-2.5 py-2 text-[10px] font-semibold">Synthetic</Link></div></Card>
      </section>

      <footer className="flex flex-col gap-3 rounded-2xl border theme-border theme-surface-2 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3"><Database size={17} className="mt-0.5 shrink-0 theme-accent" /><div><div className="text-xs font-bold">Execution Safety Boundary</div><div className="mt-1 text-[10px] leading-5 theme-muted">External broker orders remain OFF. FastAPI market data and scanner telemetry remain separate from order routing.</div></div></div>
        <div className="flex flex-wrap items-center gap-2"><span className="rounded-full border theme-border px-3 py-1.5 text-[9px] font-bold theme-warning">LIVE ORDERS OFF</span><span className="rounded-full border theme-border px-3 py-1.5 text-[9px] font-bold theme-muted">SESSION START {tickRuntimeLabel}</span></div>
      </footer>

      {connectionsOpen ? (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="connections-title" onMouseDown={(event) => { if (event.target === event.currentTarget) setConnectionsOpen(false); }}>
          <Card className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-3xl border theme-border theme-surface-2 p-5 shadow-2xl sm:p-6">
            <div className="flex items-center justify-between gap-3"><div><div className="text-[10px] font-bold uppercase tracking-[0.18em] theme-accent">Runtime telemetry</div><h2 id="connections-title" className="mt-1 text-xl font-black">Connection Details</h2></div><button type="button" onClick={() => setConnectionsOpen(false)} className="rounded-xl border theme-border px-3 py-2 text-sm" aria-label="Close connections">✕</button></div>
            <div className="mt-5 grid gap-2 sm:grid-cols-2">{[["FastAPI", apiLabel], ["Dashboard WebSocket", wsLabel], ["Common Market Feed", feedLabel], ["Ticks Received", runtime.ticks.toLocaleString("en-IN")], ["Runtime Started", tickRuntimeLabel], ["Cash-Future Runner", runtime.runners.cash_future?.detail ?? "UNAVAILABLE"], ["Synthetic Runner", runtime.runners.synthetic_arbitrage?.detail ?? "UNAVAILABLE"], ["Box Spread Runner", runtime.runners.box_spread?.detail ?? "UNAVAILABLE"], ["Calendar Runner", runtime.runners.calendar_spread?.detail ?? "UNAVAILABLE"], ["Broker Orders", "OFF"], ["Active Instruments", runtime.activeInstruments.toLocaleString("en-IN")], ["Socket Groups", runtime.connectedGroups + "/" + runtime.maxSocketSessions], ["Last Feed Tick Age", feedAgeLabel], ["Delivery / Normalizer Errors", runtime.deliveryErrors + " / " + runtime.normalizerErrors], ["NSE Session", marketSession], ["MCX Session", mcxSession]].map(([name, value]) => <div key={String(name)} className="flex items-center justify-between gap-3 rounded-xl border theme-border theme-surface px-3 py-3"><span className="text-[10px] theme-muted">{name}</span><span className="text-right text-[10px] font-bold theme-accent">{value}</span></div>)}</div>
            <div className="mt-4 rounded-2xl border theme-border theme-surface p-4"><div className="text-[10px] font-bold uppercase tracking-wider">Runtime errors recorded since API process start</div>{diagnosticErrors.length === 0 ? <p className="mt-2 text-[10px] leading-5 theme-muted">No error records returned in the latest sample. This does not independently prove zero active faults.</p> : <div className="mt-3 max-h-48 space-y-2 overflow-y-auto">{diagnosticErrors.slice(0, 10).map((error, index) => <div key={error.id ?? error.time ?? index} className="rounded-xl border theme-border p-3"><div className="flex flex-wrap justify-between gap-2 text-[10px] font-bold"><span>{error.component ?? "Runtime"} · {error.severity ?? "ERROR"}</span><span className="theme-subtle">{error.time ? new Date(error.time).toLocaleString("en-IN") : "Time unavailable"}</span></div><p className="mt-1 break-words text-[11px] theme-muted">{error.message ?? error.error_type ?? "Diagnostic event"}</p></div>)}</div>}</div>
            <div className="mt-5 flex justify-end"><Link href="/scanner" onClick={() => setConnectionsOpen(false)} className="rounded-xl theme-accent-bg px-4 py-2.5 text-[11px] font-bold theme-accent">Open Scanner ↗</Link></div>
          </Card>
        </div>
      ) : null}
    </main>
  );
}
