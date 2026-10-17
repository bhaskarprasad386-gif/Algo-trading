"use client";

import { useEffect, useState } from "react";
import { Activity, Bell, Clock3, Database, Gauge, Radio, TriangleAlert, TrendingDown, TrendingUp, Zap } from "lucide-react";
import Link from "next/link";
import { Card, PageTitle } from "@/components/ui";
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
      socket = new WebSocket(getDashboardWsUrl() + "/ws/dashboard");
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
            cashFutureObservedAt: message?.scanner?.health?.last_observation_at ?? null,
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
        const next: Record<string, { ltp: number | null; previousClose: number | null; changePercent: number | null; status: string }> = {};
        const sessionValue = body?.nse_market_session ?? body?.market_session;
        const session = sessionValue === "OPEN" ? "OPEN" : sessionValue === "CLOSED" ? "CLOSED" : "UNKNOWN";
        const mcxValue = body?.mcx_market_session;
        const mcx = mcxValue === "OPEN" ? "OPEN" : mcxValue === "CLOSED" ? "CLOSED" : "UNKNOWN";
        for (const row of Array.isArray(body?.indices) ? body.indices : []) {
          if (row?.symbol) {
            const ltp = row?.ltp == null ? null : Number(row.ltp);
            const previousClose = row?.close == null ? null : Number(row.close);
            const changePercent = ltp != null && previousClose != null && previousClose !== 0 ? ((ltp - previousClose) / previousClose) * 100 : null;
            next[String(row.symbol).toUpperCase()] = { ltp, previousClose, changePercent, status: String(row?.status ?? "NO_QUOTE").toUpperCase() };
          }
        }
        if (!cancelled) {
          setMarketSession(session);
          setMcxSession(mcx);
          setMarketCheckedAt(new Date().toISOString());
          setIndexLtps(next);
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
        else if (session !== "OPEN") setFeedStatus("no-data");
        else if (status === "LIVE" && age != null && Number.isFinite(Number(age)) && Number(age) >= 0 && Number(age) <= 5) setFeedStatus("live");
        else if (status === "STALE" || (age != null && Number.isFinite(Number(age)) && Number(age) > 5)) setFeedStatus("stale");
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
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(base + "/api/v1/market-data/runtime/health", { cache: "no-store" });
        if (!response.ok) throw new Error("runtime health");
        const body = await response.json();
        if (cancelled) return;
        const feed = body?.common_feed ?? {};
        const runners = body?.runners ?? {};
        const runnerDetail = (value: any) => {
          if (!value) return { running: false, detail: "UNAVAILABLE", updates: 0, payloads: 0, instruments: 0 };
          const running = Boolean(value.running);
          const nestedFeed = value.underlying_feed ?? value.feed ?? null;
          const instruments = Number(value.registered_instruments ?? value.active_instruments ?? nestedFeed?.active_instruments ?? nestedFeed?.subscriptions ?? 0);
          const payloads = Number(value.records_received ?? value.payload_callbacks ?? value.callbacks ?? 0);
          const updates = Number(value.scanner_updates ?? value.scanner_results ?? value.observations ?? value.signals ?? 0);
          const connected = nestedFeed?.connected_groups?.length ?? 0;
          const parts = [running ? "RUNNING" : "STOPPED"];
          if (Number.isFinite(instruments) && instruments > 0) parts.push(instruments.toLocaleString("en-IN") + " instruments");
          if (Number.isFinite(payloads) && payloads > 0) parts.push(payloads.toLocaleString("en-IN") + " payloads");
          if (Number.isFinite(updates) && updates > 0) parts.push(updates.toLocaleString("en-IN") + " scanner updates");
          if (connected > 0) parts.push(connected + " feed socket" + (connected === 1 ? "" : "s"));
          return { running, detail: parts.join(" • "), updates: Number.isFinite(updates) ? updates : 0, payloads: Number.isFinite(payloads) ? payloads : 0, instruments: Number.isFinite(instruments) ? instruments : 0 };
        };
        setRuntime({
          ticks: Number(feed.ticks_received ?? 0),
          runtimeStartedAt: feed.runtime_started_at == null ? null : Number(feed.runtime_started_at),
          activeInstruments: Number(feed.active_instruments ?? 0),
          socketGroups: Number(feed.socket_groups ?? 0),
          connectedGroups: Array.isArray(feed.connected_groups) ? feed.connected_groups.length : 0,
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
    let cancelled = false;
    const loadDiagnosticErrors = async () => {
      try {
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(base + "/api/v1/diagnostics/errors?limit=50", { cache: "no-store" });
        if (!response.ok) throw new Error("diagnostics");
        const body = await response.json();
        if (!cancelled) {
          setDiagnosticErrors(Array.isArray(body?.errors) ? body.errors : []);
          setDiagnosticCount(Number.isFinite(Number(body?.count)) ? Number(body.count) : (Array.isArray(body?.errors) ? body.errors.length : 0));
        }
      } catch {
        // Do not manufacture a fake runtime error when the diagnostic endpoint is unavailable.
      }
    };
    void loadDiagnosticErrors();
    const timer = window.setInterval(loadDiagnosticErrors, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);


  const apiLabel = apiStatus === "connected" ? "CONNECTED" : apiStatus === "error" ? "ERROR" : "CONNECTING";
  const wsLabel = wsStatus === "connected" ? "CONNECTED" : wsStatus === "error" ? "ERROR" : "CONNECTING";
  const tickRuntimeLabel = runtime.runtimeStartedAt ? new Date(runtime.runtimeStartedAt * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
  const feedLabel = feedStatus === "live" ? "LIVE" : feedStatus === "stale" ? "STALE" : feedStatus === "no-data" ? "NO DATA" : feedStatus === "closed" ? "CLOSED" : feedStatus === "error" ? "ERROR" : "CHECKING";
  const feedAgeLabel = feedAge == null ? "Not available" : feedAge >= 3600 ? `${Math.floor(feedAge / 3600)}h ${Math.floor((feedAge % 3600) / 60)}m` : feedAge >= 60 ? `${Math.floor(feedAge / 60)}m ${Math.floor(feedAge % 60)}s` : `${feedAge.toFixed(1)}s`;
  const snapshotAge = snapshot.timestamp ? Math.max(0, (Date.now() - new Date(snapshot.timestamp).getTime()) / 1000) : null;
  const observationAge = snapshot.cashFutureObservedAt ? Math.max(0, (Date.now() - new Date(snapshot.cashFutureObservedAt).getTime()) / 1000) : null;
  const observationAgeLabel = observationAge == null ? "No Cash-Future observation time" : observationAge < 60 ? `Cash-Future observation ${Math.round(observationAge)}s ago` : `Cash-Future observation stale • ${Math.round(observationAge / 60)}m ago`;
  const marketDataAge = marketCheckedAt ? Math.max(0, (Date.now() - new Date(marketCheckedAt).getTime()) / 1000) : null;
  const scannerStatus = [
    { label: "Positive Net-Gap Signals (max 50)", value: snapshot.opportunities.toLocaleString("en-IN") },
    { label: "Calendar Rows", value: snapshot.calendar.toLocaleString("en-IN") },
    { label: "Synthetic Rows", value: snapshot.synthetic.toLocaleString("en-IN") },
    { label: "Orders", value: "Not reported" },
    { label: "Fills", value: "Not reported" },
    { label: "Backend Errors", value: "Not reported" },
    { label: "Box Rows", value: snapshot.box.toLocaleString("en-IN") },
  ];

  return (
    <div className="min-h-screen theme-bg theme-text p-1">
      <PageTitle eyebrow="LIVE MARKET OPERATIONS" title="Trading Dashboard" description="Broker quote responses, feed freshness and scanner activity at a glance." />
      <div className="mb-5 grid gap-3 lg:grid-cols-[1fr_auto]">
        <Card className="rounded-2xl border theme-border theme-surface-2 p-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              {label:"NSE EQUITY", value:marketSession, detail:"09:15–15:30 IST"},
              {label:"MCX COMMODITIES", value:mcxSession, detail:"Scheduled window 09:00–23:30 IST"},
            ].map((item) => <div key={item.label} className="rounded-xl border theme-border px-3 py-2">
              <div className="text-[9px] font-bold uppercase tracking-[0.14em] theme-subtle">{item.label}</div>
              <div className={"mt-1 text-[13px] font-extrabold " + (item.value === "OPEN" ? "theme-success" : item.value === "CLOSED" ? "theme-warning" : "theme-muted")}>{item.value}</div>
              <div className="mt-0.5 text-[9px] theme-subtle">{item.detail}</div>
            </div>)}
            <div className="rounded-xl border theme-border px-3 py-2"><div className="text-[9px] font-bold uppercase tracking-[0.14em] theme-subtle">COMMON WEBSOCKET</div><div className={"mt-1 text-[13px] font-extrabold " + (runtime.connectedGroups > 0 ? "theme-success" : "theme-danger")}>{runtime.connectedGroups > 0 ? "CONNECTED" : "DISCONNECTED"}</div><div className="mt-0.5 text-[9px] theme-subtle">{runtime.connectedGroups}/{runtime.maxSocketSessions} socket groups</div></div>
            <div className="rounded-xl border theme-border px-3 py-2"><div className="text-[9px] font-bold uppercase tracking-[0.14em] theme-subtle">LIVE BROKER ORDERS</div><div className="mt-1 text-[13px] font-extrabold theme-warning">OFF</div><div className="mt-0.5 text-[9px] theme-subtle">Market data only</div></div>
          </div>
          <div className="mt-2 text-[9px] theme-subtle">Exchange session is schedule-based. Quote status confirms an API response, not the exchange tick timestamp; feed freshness comes from runtime telemetry.</div>
        </Card>
        <div className="flex flex-wrap items-start justify-end gap-2">
          <div className={"inline-flex items-center gap-2 rounded-full border theme-border px-3 py-1.5 text-[11px] font-semibold " + (feedLabel === "LIVE" ? "theme-success-bg theme-success" : "theme-warning-bg theme-warning")}><Radio size={13} /> {feedLabel === "LIVE" ? "Market feed LIVE" : feedLabel === "CLOSED" ? "Market CLOSED" : `Market feed ${feedLabel.toLowerCase()}`}</div>
        </div>
      </div>

      <section aria-label="Market overview">
        <div className="mb-3 flex items-center gap-2"><Activity size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">Market Overview</h2></div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          {markets.map((symbol) => {
            const quote = indexLtps[symbol];
            const quoteAvailable = marketDataAge != null && marketDataAge <= 15 && (marketSession === "OPEN" ? quote?.status === "LIVE" : marketSession === "CLOSED" ? quote?.status === "CLOSED_LAST_CLOSE" : false);
            const change = quoteAvailable ? quote?.changePercent : null;
            const positive = change != null && change > 0;
            const negative = change != null && change < 0;
            return (
                <Card key={symbol} className="relative overflow-hidden rounded-2xl p-4">
                  <span className="absolute inset-y-0 left-0 w-1 theme-accent-bg" />
                  <div className="pl-1">
                    <div className="flex items-center justify-between"><span className="text-[12px] font-bold tracking-wide">{symbol}</span>{marketSession === "CLOSED" ? <span className="rounded-full theme-warning-bg theme-warning px-2 py-0.5 text-[9px] font-bold">CLOSED</span> : null}{positive ? <TrendingUp size={15} className="theme-success" /> : negative ? <TrendingDown size={15} className="theme-danger" /> : <Activity size={15} className="theme-subtle" />}</div>
                    <div className="mt-3 flex items-center justify-between gap-2">
                      <div className={"text-lg font-bold " + (positive ? "theme-success" : negative ? "theme-danger" : "theme-text")}>{!quoteAvailable || quote?.ltp == null ? (marketSession === "CLOSED" ? "Last close unavailable" : "No broker quote") : quote.ltp.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                      {(!quoteAvailable || quote?.ltp == null) && <Radio size={14} className="theme-warning" aria-label="No verified quote" />}
                    </div>
                    <div className={"mt-1 text-[12px] font-bold " + (positive ? "theme-success" : negative ? "theme-danger" : "theme-muted")}>{change == null ? "No live change" : (change > 0 ? "+" : "") + change.toFixed(2) + "%"}</div>
                    <div className="mt-1 text-[10px] theme-subtle">{!quoteAvailable || quote?.previousClose == null ? "Previous close unavailable" : marketSession === "CLOSED" ? "Last close: " + quote.previousClose.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "Prev close: " + quote.previousClose.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                    <div className="mt-3 flex items-center justify-between border-t theme-border pt-2 text-[10px] font-semibold theme-muted">
                      <span>{quoteAvailable ? "Broker quote returned" : "Awaiting broker quote"}</span><span>{marketSession === "CLOSED" ? "LAST CLOSE" : quoteAvailable ? "QUOTE RESPONSE" : "NO DATA"}</span>
                    </div>
                  </div>
                </Card>
            );
          })}
        </div>
      </section>

      <div className="mt-6">
        <section aria-label="System health">
          <div className="mb-3 flex items-center justify-between gap-3"><div className="flex items-center gap-2"><Gauge size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">System Health</h2></div><div className="text-[10px] theme-subtle">Auto-refreshing live telemetry</div></div>
          <Card className="overflow-hidden rounded-2xl border theme-border theme-surface-2 p-0 shadow-sm">
            <div className="overflow-x-auto"><table className="w-full min-w-[1080px] border-collapse text-left">
              <thead className="theme-surface"><tr className="border-b theme-border text-[9px] uppercase tracking-[0.12em] theme-subtle">
                <th className="px-3 py-3 font-bold">Feed / Scanner</th><th className="px-3 py-3 font-bold">WebSocket / State</th><th className="px-3 py-3 font-bold">Live ticks / updates</th><th className="px-3 py-3 font-bold">Tick age</th><th className="px-3 py-3 font-bold">Data flow</th><th className="px-3 py-3 font-bold">Scanner position</th>
              </tr></thead><tbody>
                {[
                  {name:"OVERALL COMMON FEED", state:runtime.connectedGroups > 0 ? "CONNECTED" : "DISCONNECTED", updates:runtime.ticks, age:feedAgeLabel, flow:feedLabel, position:runtime.activeInstruments.toLocaleString("en-IN")+" active instruments"},
                  {name:"CASH MARKET DATA", state:runtime.runners.cash_future?.running ? "RUNNING" : "STOPPED", updates:runtime.runners.cash_future?.payloads ?? 0, age:feedAgeLabel, flow:(runtime.runners.cash_future?.payloads ?? 0)>0 ? "RECEIVING" : "NO UPDATES", position:runtime.runners.cash_future?.instruments ? runtime.runners.cash_future.instruments.toLocaleString("en-IN")+" instruments" : "No instrument count"},
                  {name:"OPTIONS DATA", state:(runtime.runners.box_spread?.running || runtime.runners.calendar_spread?.running) ? "RUNNING" : "STOPPED", updates:(runtime.runners.box_spread?.payloads ?? 0)+(runtime.runners.calendar_spread?.payloads ?? 0), age:feedAgeLabel, flow:((runtime.runners.box_spread?.payloads ?? 0)+(runtime.runners.calendar_spread?.payloads ?? 0))>0 ? "RECEIVING" : "NO UPDATES", position:"Box + Calendar feed"},
                  {name:"FUTURES DATA", state:runtime.runners.synthetic_arbitrage?.running ? "RUNNING" : "STOPPED", updates:runtime.runners.synthetic_arbitrage?.payloads ?? 0, age:feedAgeLabel, flow:(runtime.runners.synthetic_arbitrage?.payloads ?? 0)>0 ? "RECEIVING" : "NO UPDATES", position:"Synthetic feed"},
                  {name:"CASH-FUTURE SCANNER", state:runtime.runners.cash_future?.running ? "RUNNING" : "STOPPED", updates:runtime.runners.cash_future?.updates ?? 0, age:observationAge == null ? "—" : observationAge < 60 ? Math.round(observationAge)+"s" : Math.round(observationAge/60)+"m", flow:(runtime.runners.cash_future?.updates ?? 0)>0 ? "RECEIVING" : "NO UPDATES", position:runtime.runners.cash_future?.detail ?? "UNAVAILABLE"},
                  {name:"SYNTHETIC ARBITRAGE", state:runtime.runners.synthetic_arbitrage?.running ? "RUNNING" : "STOPPED", updates:runtime.runners.synthetic_arbitrage?.updates ?? 0, age:feedAgeLabel, flow:(runtime.runners.synthetic_arbitrage?.updates ?? 0)>0 ? "RECEIVING" : "NO UPDATES", position:runtime.runners.synthetic_arbitrage?.detail ?? "UNAVAILABLE"},
                  {name:"BOX SPREAD SCANNER", state:runtime.runners.box_spread?.running ? "RUNNING" : "STOPPED", updates:runtime.runners.box_spread?.updates ?? 0, age:feedAgeLabel, flow:(runtime.runners.box_spread?.updates ?? 0)>0 ? "RECEIVING" : "NO UPDATES", position:runtime.runners.box_spread?.detail ?? "UNAVAILABLE"},
                  {name:"CALENDAR SPREAD SCANNER", state:runtime.runners.calendar_spread?.running ? "RUNNING" : "STOPPED", updates:runtime.runners.calendar_spread?.updates ?? 0, age:feedAgeLabel, flow:(runtime.runners.calendar_spread?.updates ?? 0)>0 ? "RECEIVING" : "NO UPDATES", position:runtime.runners.calendar_spread?.detail ?? "UNAVAILABLE"},
                ].map((row) => {
                  const healthy = row.state === "CONNECTED" || row.flow === "LIVE" || row.flow === "RECEIVING";
                  return <tr key={row.name} className="border-b theme-border last:border-0">
                    <td className="px-3 py-3 text-[10px] font-bold">{row.name}</td><td className="px-3 py-3"><span className={"rounded-full px-2 py-1 text-[9px] font-bold "+(healthy ? "theme-success-bg theme-success" : "theme-warning-bg theme-warning")}>{row.state}</span></td>
                    <td className="px-3 py-3 text-[11px] font-bold tabular-nums">{Number(row.updates).toLocaleString("en-IN")}</td><td className="px-3 py-3 text-[10px] font-semibold theme-muted">{row.age}</td>
                    <td className="px-3 py-3 text-[9px] font-bold"><span className={row.flow === "RECEIVING" || row.flow === "LIVE" ? "theme-success" : "theme-warning"}>{row.flow}</span></td><td className="max-w-[360px] px-3 py-3 text-[10px] theme-muted">{row.position}</td>
                  </tr>;
                })}
              </tbody></table></div>
          </Card>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">{[
            ["Overall live ticks", runtime.ticks.toLocaleString("en-IN")],["Active instruments", runtime.activeInstruments.toLocaleString("en-IN")],
            ["Connected sockets", runtime.connectedGroups+"/"+runtime.maxSocketSessions],["Delivery / normalizer errors", runtime.deliveryErrors+" / "+runtime.normalizerErrors],
          ].map(([label,value]) => <div key={label} className="rounded-xl border theme-border theme-surface-2 px-3 py-2"><div className="text-[9px] uppercase tracking-wide theme-subtle">{label}</div><div className="mt-1 text-[11px] font-bold">{value}</div></div>)}</div>
        </section>
      </div>

      <section aria-label="Error Diagnostics" className="mt-4">
        <Card className="rounded-2xl border theme-border theme-surface-2 p-3">
          <div className="grid grid-cols-[1fr_auto] items-center gap-3">
            <div className="flex min-w-0 items-center gap-3">
              <span className={diagnosticErrors.length ? "grid h-10 w-10 shrink-0 place-items-center rounded-xl theme-danger-bg theme-danger" : "grid h-10 w-10 shrink-0 place-items-center rounded-xl theme-success-bg theme-success"}>
                <TriangleAlert size={18} />
              </span>
              <div className="min-w-0">
                <div className="text-[13px] font-bold">Error Diagnostics</div>
                <div className="mt-0.5 text-[10px] theme-subtle">
                  {diagnosticCount ? `${diagnosticCount.toLocaleString("en-IN")} runtime errors recorded since API process start (not an active-error count)` : "No errors recorded by this endpoint since API process start"}
                </div>
              </div>
            </div>
            <Link href="/errors" className="inline-flex min-h-10 items-center justify-center rounded-xl theme-accent-bg px-4 text-[11px] font-bold theme-accent">
              View Errors
            </Link>
          </div>
        </Card>
      </section>
      <div className="mt-6 grid gap-4 xl:grid-cols-[1.55fr_1fr]">
        <section aria-label="Strategy workspaces">
          <div className="mb-3 flex items-center gap-2"><Zap size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">Strategy Workspaces</h2></div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {strategies.map((strategy) => { const Icon = strategy.icon; return <Link key={strategy.name} href={strategy.href} className="block"><Card className="min-h-[95px] rounded-2xl p-3 transition hover:-translate-y-0.5"><div className="flex items-center gap-2"><span className="grid h-8 w-8 place-items-center rounded-lg theme-accent-bg theme-accent"><Icon size={15} /></span><div className="text-[12px] font-semibold">{strategy.name}</div></div><div className="mt-2 text-[10px] theme-subtle">{strategy.detail} —</div></Card></Link>; })}
          </div>
        </section>
        <div className="space-y-4">
          <section aria-label="Scanner status"><div className="mb-3 flex items-center justify-between gap-2"><div className="flex items-center gap-2"><Radio size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">Scanner Status</h2></div><div className="text-[10px] theme-subtle">{snapshotAge == null ? "No dashboard heartbeat yet" : snapshotAge < 10 ? `Heartbeat ${Math.round(snapshotAge)}s ago` : `Heartbeat stale • ${Math.round(snapshotAge)}s ago`} • {observationAgeLabel}</div></div><div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{scannerStatus.map((item) => <Card key={item.label} className="rounded-xl p-2.5"><div className="text-[10px] font-medium theme-subtle">{item.label}</div><div className="mt-1 text-[11px] font-semibold">{item.value}</div></Card>)}</div></section>
          <section aria-label="Operations"><div className="mb-3 flex items-center gap-2"><Zap size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">Operations</h2></div><div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Link href="/scanner"><Card className="rounded-2xl p-3 transition hover:-translate-y-0.5"><div className="flex gap-2"><Radio size={16} className="theme-accent" /><div><div className="text-[12px] font-semibold">Cash Future</div><div className="text-[10px] theme-subtle">Open Cash Future scanner</div></div></div></Card></Link>
            <Link href="/custom-alert"><Card className="rounded-2xl p-3 transition hover:-translate-y-0.5"><div className="flex gap-2"><Bell size={16} className="theme-warning" /><div><div className="text-[12px] font-semibold">Custom Alerts</div><div className="text-[10px] theme-subtle">Manage alerts</div></div></div></Card></Link>
            <Link href="/history"><Card className="rounded-2xl p-3 transition hover:-translate-y-0.5"><div className="flex gap-2"><Clock3 size={16} className="theme-accent" /><div><div className="text-[12px] font-semibold">History</div><div className="text-[10px] theme-subtle">Trade history & logs</div></div></div></Card></Link>
          </div></section>
        </div>
      </div>

      <Card className="mt-6 flex flex-col gap-3 rounded-2xl p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-start gap-3"><Database size={17} className="mt-0.5 shrink-0 theme-accent" /><div><div className="text-[13px] font-semibold">Integration Boundary</div><div className="text-[11px] theme-subtle">External broker orders remain OFF. FastAPI market data runs independently of broker order routing.</div></div></div>
        <button type="button" onClick={() => setConnectionsOpen(true)} className="shrink-0 rounded-xl theme-accent-bg px-3 py-2 text-[11px] font-bold theme-accent">View Connections</button>
      </Card>
      {connectionsOpen ? (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="connections-title" onMouseDown={(event) => { if (event.target === event.currentTarget) setConnectionsOpen(false); }}>
          <Card className="w-full max-w-xl rounded-2xl border theme-border theme-surface-2 p-5 shadow-2xl">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div id="connections-title" className="text-[14px] font-bold">Connections</div>
                <div className="mt-1 text-[10px] theme-subtle">Current frontend connection state</div>
              </div>
              <button type="button" onClick={() => setConnectionsOpen(false)} className="rounded-lg px-2 py-1 text-xs theme-muted" aria-label="Close connections">✕</button>
            </div>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {[
                ["FastAPI", apiLabel],
                ["Dashboard WebSocket", wsLabel],
                ["Common Market Data Feed", feedLabel],
                ["Ticks Received (session)", runtime.ticks.toLocaleString("en-IN")],
                ["Tick Counter", runtime.runtimeStartedAt ? `Session • ${tickRuntimeLabel}` : "—"],
                ["Cash Future Runner", runtime.runners.cash_future?.detail ?? "UNAVAILABLE"],
                ["Synthetic Future Runner", runtime.runners.synthetic_arbitrage?.detail ?? "UNAVAILABLE"],
                ["Box Spread Runner", runtime.runners.box_spread?.detail ?? "UNAVAILABLE"],
                ["Calendar Spread Runner", runtime.runners.calendar_spread?.detail ?? "UNAVAILABLE"],
                ["Broker Orders", "OFF"],
                ["Common Feed Instruments", runtime.activeInstruments.toLocaleString("en-IN")],
                ["Socket Groups", runtime.socketGroups + "/" + runtime.maxSocketSessions],
                ["Last Feed Tick Age", feedAgeLabel],
                ["Delivery / Normalizer Errors", runtime.deliveryErrors + " / " + runtime.normalizerErrors],
                ["NSE Session", marketSession],
                ["MCX Session", mcxSession],
              ].map(([name, value]) => (
                <div key={name} className="flex items-center justify-between rounded-xl border theme-border theme-surface-2 px-3 py-2.5">
                  <span className="text-[11px] theme-muted">{name}</span>
                  <span className="text-[10px] font-bold theme-accent">{value}</span>
                </div>
              ))}
            </div>
            <div className="mt-4 rounded-xl border theme-border theme-surface-2 p-3">
              <div className="text-[10px] font-bold uppercase tracking-wide">Runtime Errors Recorded Since API Process Start</div>
              {diagnosticErrors.length === 0 ? <div className="mt-2 text-[10px] theme-muted">No error records returned in the latest sample. This does not independently prove zero active faults.</div> : (
                <div className="mt-2 max-h-48 space-y-2 overflow-auto">
                  {diagnosticErrors.map((error) => (
                    <div key={error.id} className="rounded-lg border theme-border px-2.5 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[10px] font-bold">{error.component} • {error.severity}</span>
                        <span className="text-[9px] theme-subtle">{new Date(error.time).toLocaleString("en-IN")}</span>
                      </div>
                      <div className="mt-1 text-[10px]">{error.message}</div>
                      <div className="mt-1 text-[9px] theme-subtle">{[error.error_type && "Type: " + error.error_type, error.endpoint && "Endpoint: " + error.endpoint, error.status_code && "HTTP: " + error.status_code].filter(Boolean).join(" • ")}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="mt-4 rounded-xl border theme-border theme-accent-bg p-3"><div className="text-[10px] font-bold uppercase tracking-wide theme-accent">Common Feed Diagnostics • Session Counters</div><div className="mt-2 grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-4"><span>Consumers: <b>{runtime.consumers.length}</b></span><span>Delivery errors: <b>{runtime.deliveryErrors}</b></span><span>Normalizer errors: <b>{runtime.normalizerErrors}</b></span><span>Market: <b>{marketSession}</b></span></div></div><div className="mt-4 flex justify-end">
              <Link href="/scanner" onClick={() => setConnectionsOpen(false)} className="rounded-xl theme-accent-bg px-3 py-2 text-[11px] font-bold theme-accent">Open Scanner</Link>
            </div>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
