"use client";

import { useEffect, useState } from "react";
import { Activity, Bell, Clock3, Database, Gauge, Radio, Save, ShieldCheck, TriangleAlert, TrendingDown, TrendingUp, WalletCards, Zap } from "lucide-react";
import Link from "next/link";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

const markets = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"];
const strategies = [
  { name: "Cash Future", detail: "Live Scanner • F&O •", href: "/scanner", icon: Radio },
  { name: "Calendar Spread", detail: "Time spread • NIFTY •", href: "/strategies/calendar-spread", icon: Activity },
  { name: "Synthetic Arbitrage", detail: "Cash-Synth • Arbitrage •", href: "/strategies/synthetic-arbitrage", icon: Zap },
  { name: "Box Spread", detail: "Options • Box •", href: "/strategies/box-spread", icon: Gauge },
  { name: "Custom Strategy", detail: "Independent • User-defined •", href: "/strategies/custom-strategy", icon: Activity },
  { name: "Strategy Scanner", detail: "Dedicated workspaces •", href: "/scanner", icon: Radio },
  { name: "Broker Orders", detail: "Always OFF • Paper safe •", href: "/paper-trading", icon: ShieldCheck },
];

const health = (api: string, ws: string, feed: string, ticks: number | null, runners: Record<string, { running: boolean; detail: string }>) => [
  ["Frontend", "READY", "ok"],
  ["FastAPI", api, api === "CONNECTED" ? "ok" : api === "ERROR" ? "error" : "warn"],
  ["Dashboard WS", ws, ws === "CONNECTED" ? "ok" : ws === "ERROR" ? "error" : "warn"],
  ["Common Market Data Feed", feed, feed === "LIVE" ? "ok" : feed === "ERROR" ? "error" : "warn"],
  ["Live Ticks", ticks == null ? "—" : ticks.toLocaleString("en-IN"), ticks && ticks > 0 ? "ok" : "warn"],
  ["Cash Future Runner", runners.cash_future?.detail ?? "—", runners.cash_future?.running ? "ok" : "warn"],
  ["Synthetic Future Runner", runners.synthetic_arbitrage?.detail ?? "—", runners.synthetic_arbitrage?.running ? "ok" : "warn"],
  ["Box Spread Runner", runners.box_spread?.detail ?? "—", runners.box_spread?.running ? "ok" : "warn"],
  ["Calendar Spread Runner", runners.calendar_spread?.detail ?? "—", runners.calendar_spread?.running ? "ok" : "warn"],
  ["Broker Orders", "OFF", "warn"],
] as const;

export default function HomePage() {
  const [capital, setCapital] = useState("10000000");
  const [capitalDraft, setCapitalDraft] = useState("10000000");
  const [capitalSaved, setCapitalSaved] = useState(false);
  const [apiStatus, setApiStatus] = useState<"checking" | "connected" | "error">("checking");
  const [wsStatus, setWsStatus] = useState<"connecting" | "connected" | "error">("connecting");
  const [snapshot, setSnapshot] = useState({ calendar: 0, synthetic: 0, box: 0, opportunities: 0, timestamp: null as string | null });
  const [indexLtps, setIndexLtps] = useState<Record<string, { ltp: number | null; previousClose: number | null; changePercent: number | null }>>({});
  const [marketSession, setMarketSession] = useState<"OPEN" | "CLOSED" | "UNKNOWN">("UNKNOWN");
  const [marketCheckedAt, setMarketCheckedAt] = useState<string | null>(null);
  const [feedStatus, setFeedStatus] = useState<"checking" | "live" | "stale" | "no-data" | "closed" | "error">("checking");
  const [feedAge, setFeedAge] = useState<number | null>(null);
  const [runtime, setRuntime] = useState<{ticks: number; activeInstruments: number; socketGroups: number; maxSocketSessions: number; consumers: string[]; deliveryErrors: number; normalizerErrors: number; runners: Record<string, {running: boolean; detail: string}>}>({ticks: 0, activeInstruments: 0, socketGroups: 0, maxSocketSessions: 3, consumers: [], deliveryErrors: 0, normalizerErrors: 0, runners: {}});
  const [connectionsOpen, setConnectionsOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetch(appConfig.apiBaseUrl.replace(/\/$/, "") + "/health", { cache: "no-store", signal: controller.signal })
      .then((response) => { if (!response.ok) throw new Error("health"); setApiStatus("connected"); })
      .catch(() => setApiStatus("error"));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    let cancelled = false;
    let socket: WebSocket | null = null;
    let retryTimer: number | null = null;
    let retryMs = 1000;

    const connect = () => {
      if (cancelled) return;
      setWsStatus("connecting");
      socket = new WebSocket(appConfig.wsUrl.replace(/\/$/, "") + "/ws/dashboard");
      socket.onopen = () => {
        retryMs = 1000;
        setWsStatus("connected");
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message?.type !== "dashboard_snapshot") return;
          setSnapshot({
            calendar: Number(message?.integration?.calendar_spread?.count ?? 0),
            synthetic: Number(message?.integration?.synthetic_arbitrage?.count ?? 0),
            box: Number(message?.integration?.box_spread?.count ?? 0),
            opportunities: Number(message?.scanner?.opportunity_count ?? 0),
            timestamp: message?.timestamp ?? null,
          });
        } catch {
          // Ignore malformed dashboard messages without losing the last valid snapshot.
        }
      };
      socket.onerror = () => setWsStatus("error");
      socket.onclose = () => {
        if (cancelled) return;
        setWsStatus("error");
        retryTimer = window.setTimeout(connect, retryMs);
        retryMs = Math.min(retryMs * 2, 10000);
      };
    };

    connect();
    return () => {
      cancelled = true;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadMarketOverview = async () => {
      try {
        const response = await fetch(appConfig.apiBaseUrl.replace(/\/$/, "") + "/api/v1/market-data/overview", { cache: "no-store" });
        if (!response.ok) throw new Error("market overview");
        const body = await response.json();
        const next: Record<string, { ltp: number | null; previousClose: number | null; changePercent: number | null }> = {};
        const session = body?.market_session === "OPEN" ? "OPEN" : body?.market_session === "CLOSED" ? "CLOSED" : "UNKNOWN";
        for (const row of Array.isArray(body?.indices) ? body.indices : []) {
          if (row?.symbol) {
            const ltp = row?.ltp == null ? null : Number(row.ltp);
            const previousClose = row?.close == null ? null : Number(row.close);
            const changePercent = ltp != null && previousClose != null && previousClose !== 0 ? ((ltp - previousClose) / previousClose) * 100 : null;
            next[String(row.symbol).toUpperCase()] = { ltp, previousClose, changePercent };
          }
        }
        if (!cancelled) {
          setMarketSession(session);
          setMarketCheckedAt(new Date().toISOString());
          setIndexLtps((previous) => {
            const merged = { ...previous };
            for (const [symbol, value] of Object.entries(next)) {
              const prior = previous[symbol];
              merged[symbol] = {
                ltp: value.ltp ?? prior?.ltp ?? null,
                previousClose: value.previousClose ?? prior?.previousClose ?? null,
                changePercent: value.changePercent ?? prior?.changePercent ?? null,
              };
            }
            return merged;
          });
        }
      } catch {
        // Keep the last successful overview snapshot during transient API/quote failures.
      }
    };
    loadMarketOverview();
    const timer = window.setInterval(loadMarketOverview, 5000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadFeedHealth = async () => {
      try {
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(`${base}/api/v1/market-data/live-health`, { cache: "no-store" });
        if (!response.ok) throw new Error("feed health");
        const body = await response.json();
        if (cancelled) return;
        const status = String(body?.feed_status ?? "").toUpperCase();
        const session = String(body?.market_session ?? "").toUpperCase();
        const age = body?.runtime_feed?.age_seconds ?? body?.age_seconds;
        setFeedAge(age == null || !Number.isFinite(Number(age)) ? null : Number(age));
        if (session === "CLOSED") setFeedStatus("closed");
        else if (status === "LIVE") setFeedStatus("live");
        else if (status === "STALE") setFeedStatus("stale");
        else setFeedStatus("no-data");
      } catch {
        if (!cancelled) setFeedStatus("error");
      }
    };
    void loadFeedHealth();
    const timer = window.setInterval(loadFeedHealth, 5000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadRuntimeHealth = async () => {
      try {
        const base = appConfig.apiBaseUrl.replace(/\\/$/, "");
        const response = await fetch(base + "/api/v1/market-data/runtime/health", { cache: "no-store" });
        if (!response.ok) throw new Error("runtime health");
        const body = await response.json();
        if (cancelled) return;
        const feed = body?.common_feed ?? {};
        const runners = body?.runners ?? {};
        const runnerDetail = (value: any) => {
          if (!value) return { running: false, detail: "UNAVAILABLE" };
          const running = Boolean(value.running);
          const instruments = Number(value.registered_instruments ?? value.active_instruments ?? 0);
          const updates = Number(value.scanner_updates ?? value.records_received ?? value.callbacks ?? 0);
          const parts = [running ? "RUNNING" : "STOPPED"];
          if (Number.isFinite(instruments) && instruments > 0) parts.push(instruments.toLocaleString("en-IN") + " instruments");
          if (Number.isFinite(updates) && updates > 0) parts.push(updates.toLocaleString("en-IN") + " updates");
          return { running, detail: parts.join(" • ") };
        };
        setRuntime({
          ticks: Number(feed.ticks_received ?? 0),
          activeInstruments: Number(feed.active_instruments ?? 0),
          socketGroups: Number(feed.socket_groups ?? 0),
          maxSocketSessions: Number(feed.max_socket_sessions ?? 3),
          consumers: Array.isArray(feed.consumers) ? feed.consumers : [],
          deliveryErrors: Number(feed.delivery_errors ?? 0),
          normalizerErrors: Number(feed.normalizer_errors ?? 0),
          runners: {
            cash_future: runnerDetail(runners.cash_future),
            synthetic_arbitrage: runnerDetail(runners.synthetic_arbitrage),
            box_spread: runnerDetail(runners.box_spread),
            calendar_spread: runnerDetail(runners.calendar_spread),
          },
        });
      } catch {
        // Keep the last successful runtime snapshot during transient API failures.
      }
    };
    void loadRuntimeHealth();
    const timer = window.setInterval(loadRuntimeHealth, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    const saved = window.localStorage.getItem("algo-paper-capital");
    if (saved && Number(saved) > 0) {
      setCapital(saved);
      setCapitalDraft(saved);
    }
  }, []);

  const saveCapital = () => {
    const normalized = capitalDraft.replace(/[^0-9]/g, "");
    if (Number(normalized) <= 0) return;
    setCapital(normalized);
    setCapitalDraft(normalized);
    window.localStorage.setItem("algo-paper-capital", normalized);
    setCapitalSaved(true);
    window.setTimeout(() => setCapitalSaved(false), 1800);
  };

  const formattedCapital = Number(capital || 0).toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
  const apiLabel = apiStatus === "connected" ? "CONNECTED" : apiStatus === "error" ? "ERROR" : "CONNECTING";
  const wsLabel = wsStatus === "connected" ? "CONNECTED" : wsStatus === "error" ? "ERROR" : "CONNECTING";
  const feedLabel = feedStatus === "live" ? "LIVE" : feedStatus === "stale" ? "STALE" : feedStatus === "no-data" ? "NO DATA" : feedStatus === "closed" ? "CLOSED" : feedStatus === "error" ? "ERROR" : "CHECKING";
  const snapshotAge = snapshot.timestamp ? Math.max(0, (Date.now() - new Date(snapshot.timestamp).getTime()) / 1000) : null;
  const marketDataAge = marketCheckedAt ? Math.max(0, (Date.now() - new Date(marketCheckedAt).getTime()) / 1000) : null;
  const scannerStatus = [
    { label: "Signals Detected", value: snapshot.opportunities.toLocaleString("en-IN") },
    { label: "Calendar Rows", value: snapshot.calendar.toLocaleString("en-IN") },
    { label: "Synthetic Rows", value: snapshot.synthetic.toLocaleString("en-IN") },
    { label: "Orders", value: "Not reported" },
    { label: "Fills", value: "Not reported" },
    { label: "Backend Errors", value: "Not reported" },
    { label: "Box Rows", value: snapshot.box.toLocaleString("en-IN") },
  ];

  return (
    <div className="min-h-screen theme-bg theme-text p-1">
      <PageTitle eyebrow="Phase 2 • Home / Command Center" title="Command Center" description="" />
      <div className="mb-5 flex flex-wrap justify-end gap-2">
        <div className="inline-flex items-center gap-2 rounded-full border theme-border theme-accent-bg px-3 py-1.5 text-[11px] font-semibold theme-accent"><Radio size={13} /> {feedLabel === "LIVE" ? "Live market feed LIVE" : feedLabel === "CLOSED" ? "Market closed" : `Market feed ${feedLabel.toLowerCase()}`}</div>
        <div className="inline-flex items-center gap-2 rounded-full border theme-border theme-success-bg px-3 py-1.5 text-[11px] font-semibold theme-success"><ShieldCheck size={13} /> Paper-safe broker orders OFF</div>
      </div>

      <section aria-label="Market overview">
        <div className="mb-3 flex items-center gap-2"><Activity size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">Market Overview</h2></div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          {markets.map((symbol) => {
            const change = indexLtps[symbol]?.changePercent;
            const positive = change != null && change > 0;
            const negative = change != null && change < 0;
            return (
              <Link key={symbol} href="/scanner" className="block">
                <Card className="relative overflow-hidden rounded-2xl p-4 transition hover:-translate-y-0.5">
                  <span className="absolute inset-y-0 left-0 w-1 theme-accent-bg" />
                  <div className="pl-1">
                    <div className="flex items-center justify-between"><span className="text-[12px] font-bold tracking-wide">{symbol}</span>{marketSession === "CLOSED" ? <span className="rounded-full theme-warning-bg theme-warning px-2 py-0.5 text-[9px] font-bold">CLOSED</span> : null}{positive ? <TrendingUp size={15} className="theme-success" /> : negative ? <TrendingDown size={15} className="theme-danger" /> : <Activity size={15} className="theme-subtle" />}</div>
                    <div className="mt-3 flex items-center justify-between gap-2">
                      <div className={"text-lg font-bold " + (positive ? "theme-success" : negative ? "theme-danger" : "theme-text")}>{indexLtps[symbol]?.ltp == null ? "No live data" : indexLtps[symbol]!.ltp!.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                      {indexLtps[symbol]?.ltp == null && <Radio size={14} className="theme-warning" aria-label="No live data" />}
                    </div>
                    <div className={"mt-1 text-[12px] font-bold " + (positive ? "theme-success" : negative ? "theme-danger" : "theme-muted")}>{change == null ? "No live change" : (change > 0 ? "+" : "") + change.toFixed(2) + "%"}</div>
                    <div className="mt-1 text-[10px] theme-subtle">{indexLtps[symbol]?.previousClose == null ? "Previous close unavailable" : marketSession === "CLOSED" ? "Last close: " + indexLtps[symbol]!.previousClose!.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "Prev close: " + indexLtps[symbol]!.previousClose!.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                    <div className="mt-3 flex items-center justify-between border-t theme-border pt-2 text-[10px] font-semibold theme-accent">
                      <span>Open Live Scanner</span><Radio size={13} />
                    </div>
                  </div>
                </Card>
              </Link>
            );
          })}
        </div>
      </section>

      <div className="mt-6 grid gap-4 xl:grid-cols-[1.55fr_1fr]">
        <section aria-label="Paper ledger">
          <div className="mb-3 flex items-center gap-2"><WalletCards size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">Paper Ledger</h2></div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Card className="rounded-2xl p-4">
              <div className="flex items-center justify-between"><div className="text-[11px] font-semibold uppercase tracking-wide theme-subtle">Paper Capital</div><WalletCards size={15} className="theme-accent" /></div>
              <div className="mt-1 text-[16px] font-bold">{formattedCapital}</div>
              <input inputMode="numeric" value={capitalDraft} onChange={(event) => { setCapitalDraft(event.target.value.replace(/[^0-9]/g, "")); setCapitalSaved(false); }} aria-label="Manual paper capital amount" className="mt-3 min-h-10 w-full px-3 text-xs" />
              <button type="button" onClick={saveCapital} disabled={!capitalDraft || Number(capitalDraft) <= 0} className="mt-2 inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-xl bg-[var(--app-accent)] px-3 text-xs font-bold theme-text transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"><Save size={14} /> {capitalSaved ? "Saved" : "Save"}</button>
            </Card>
            <Card className="rounded-2xl p-4"><div className="flex gap-6"><div><div className="text-[11px] font-semibold theme-subtle">Available Balance</div><div className="mt-1 text-sm font-semibold">—</div></div><div><div className="text-[11px] font-semibold theme-subtle">Today&apos;s P&amp;L</div><div className="mt-1 text-sm font-semibold">—</div></div></div></Card>
            <Card className="rounded-2xl p-4"><div className="text-[11px] font-semibold theme-subtle">Open Positions</div><div className="mt-1 text-sm font-semibold">—</div></Card>
          </div>
        </section>

        <section aria-label="System health">
          <div className="mb-3 flex items-center gap-2"><Gauge size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">System Health</h2></div>
          <Card className="rounded-2xl p-2">
            {health(apiLabel, wsLabel, feedLabel, runtime.ticks, runtime.runners).map(([name, value, state]) => (
              <div key={name} className="flex items-center justify-between rounded-xl px-2 py-2">
                <div className="flex items-center gap-2 text-[12px] font-medium theme-muted">{state === "error" ? <TriangleAlert size={14} className="theme-danger" /> : state === "warn" ? <TriangleAlert size={14} className="theme-warning" /> : <span className="h-2 w-2 rounded-full bg-[var(--app-success)]" />}{name}</div>
                <span className={"rounded-full px-2.5 py-1 text-[10px] font-bold " + (state === "ok" ? "theme-success-bg theme-success" : state === "warn" ? "theme-warning-bg theme-warning" : "theme-danger-bg theme-danger")}>{value}</span>
              </div>
            ))}
          </Card>
          <div className="mt-2 text-[10px] theme-subtle">Ticks: {runtime.ticks.toLocaleString("en-IN")} • Instruments: {runtime.activeInstruments.toLocaleString("en-IN")} • Sockets: {runtime.socketGroups}/{runtime.maxSocketSessions} • Feed age: {feedAge == null ? "—" : `${feedAge.toFixed(1)}s`} • Errors: {runtime.deliveryErrors + runtime.normalizerErrors}</div>
        </section>
      </div>

      <div className="mt-6 grid gap-4 xl:grid-cols-[1.55fr_1fr]">
        <section aria-label="Strategy workspaces">
          <div className="mb-3 flex items-center gap-2"><Zap size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">Strategy Workspaces</h2></div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {strategies.map((strategy) => { const Icon = strategy.icon; return <Link key={strategy.name} href={strategy.href} className="block"><Card className="min-h-[95px] rounded-2xl p-3 transition hover:-translate-y-0.5"><div className="flex items-center gap-2"><span className="grid h-8 w-8 place-items-center rounded-lg theme-accent-bg theme-accent"><Icon size={15} /></span><div className="text-[12px] font-semibold">{strategy.name}</div></div><div className="mt-2 text-[10px] theme-subtle">{strategy.detail} —</div></Card></Link>; })}
          </div>
        </section>
        <div className="space-y-4">
          <section aria-label="Scanner status"><div className="mb-3 flex items-center justify-between gap-2"><div className="flex items-center gap-2"><Radio size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">Scanner Status</h2></div><div className="text-[10px] theme-subtle">{snapshotAge == null ? "No snapshot yet" : snapshotAge < 10 ? `Snapshot ${Math.round(snapshotAge)}s ago` : `Snapshot stale • ${Math.round(snapshotAge)}s ago`}</div></div><div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{scannerStatus.map((item) => <Card key={item.label} className="rounded-xl p-2.5"><div className="text-[10px] font-medium theme-subtle">{item.label}</div><div className="mt-1 text-[11px] font-semibold">{item.value}</div></Card>)}</div></section>
          <section aria-label="Operations"><div className="mb-3 flex items-center gap-2"><Zap size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">Operations</h2></div><div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Link href="/scanner"><Card className="rounded-2xl p-3 transition hover:-translate-y-0.5"><div className="flex gap-2"><Radio size={16} className="theme-accent" /><div><div className="text-[12px] font-semibold">Cash Future</div><div className="text-[10px] theme-subtle">Open Cash Future scanner</div></div></div></Card></Link>
            <Link href="/custom-alert"><Card className="rounded-2xl p-3 transition hover:-translate-y-0.5"><div className="flex gap-2"><Bell size={16} className="theme-warning" /><div><div className="text-[12px] font-semibold">Custom Alerts</div><div className="text-[10px] theme-subtle">Manage alerts</div></div></div></Card></Link>
            <Link href="/history"><Card className="rounded-2xl p-3 transition hover:-translate-y-0.5"><div className="flex gap-2"><Clock3 size={16} className="theme-accent" /><div><div className="text-[12px] font-semibold">History</div><div className="text-[10px] theme-subtle">Trade history & logs</div></div></div></Card></Link>
          </div></section>
        </div>
      </div>

      <Card className="mt-6 flex flex-col gap-3 rounded-2xl p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-start gap-3"><Database size={17} className="mt-0.5 shrink-0 theme-accent" /><div><div className="text-[13px] font-semibold">Integration Boundary</div><div className="text-[11px] theme-subtle">External broker orders remain OFF. FastAPI market data and paper-trading services are connected independently.</div></div></div>
        <button type="button" onClick={() => setConnectionsOpen(true)} className="shrink-0 rounded-xl theme-accent-bg px-3 py-2 text-[11px] font-bold theme-accent">View Connections</button>
      </Card>
      {connectionsOpen ? (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="connections-title" onMouseDown={(event) => { if (event.target === event.currentTarget) setConnectionsOpen(false); }}>
          <Card className="w-full max-w-md rounded-2xl p-5 shadow-2xl">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div id="connections-title" className="text-[14px] font-bold">Connections</div>
                <div className="mt-1 text-[10px] theme-subtle">Current frontend connection state</div>
              </div>
              <button type="button" onClick={() => setConnectionsOpen(false)} className="rounded-lg px-2 py-1 text-xs theme-muted" aria-label="Close connections">✕</button>
            </div>
            <div className="mt-4 space-y-2">
              {[
                ["FastAPI", apiLabel],
                ["Dashboard WebSocket", wsLabel],
                ["Common Market Data Feed", feedLabel],
                ["Live Ticks", runtime.ticks.toLocaleString("en-IN")],
                ["Cash Future Runner", runtime.runners.cash_future?.detail ?? "UNAVAILABLE"],
                ["Synthetic Future Runner", runtime.runners.synthetic_arbitrage?.detail ?? "UNAVAILABLE"],
                ["Box Spread Runner", runtime.runners.box_spread?.detail ?? "UNAVAILABLE"],
                ["Calendar Spread Runner", runtime.runners.calendar_spread?.detail ?? "UNAVAILABLE"],
                ["Broker Orders", "OFF"],
              ].map(([name, value]) => (
                <div key={name} className="flex items-center justify-between rounded-xl border theme-border px-3 py-2.5">
                  <span className="text-[11px] theme-muted">{name}</span>
                  <span className="text-[10px] font-bold theme-accent">{value}</span>
                </div>
              ))}
            </div>
            <div className="mt-4 flex justify-end">
              <Link href="/scanner" onClick={() => setConnectionsOpen(false)} className="rounded-xl theme-accent-bg px-3 py-2 text-[11px] font-bold theme-accent">Open Scanner</Link>
            </div>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
